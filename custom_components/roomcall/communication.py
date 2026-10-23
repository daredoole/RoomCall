"""One validated control surface for humans, HA actions, HTTP and LLM tools."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from homeassistant.auth.permissions.const import POLICY_CONTROL, POLICY_READ
from homeassistant.components.homeassistant import async_should_expose
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .calls import CallError, CallRequest, prepare_call
from .capabilities import discover
from .const import DOMAIN
from .core import resolve
from .errors import Problem
from .protocol import API_VERSION, SETUP_KEY
from .schema import vol

TARGETS = {
    vol.Optional("areas"): vol.All(cv.ensure_list, [cv.string]),
    vol.Optional("speakers"): cv.entity_ids,
    vol.Optional("people"): cv.entity_ids,
    vol.Optional("everywhere", default=False): cv.boolean,
    vol.Optional("priority", default="normal"): vol.In(("normal", "urgent")),
    vol.Optional("volume"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
}
SPEECH_PROFILE = vol.In(("fast", "balanced", "quality"))
MESSAGE = {
    vol.Optional("durable", default=False): cv.boolean,
    vol.Optional("speech_profile"): SPEECH_PROFILE,
    **TARGETS,
    vol.Optional("message"): vol.All(cv.string, vol.Length(min=1, max=4000)),
    vol.Optional("media_url"): vol.All(cv.string, vol.Length(min=1, max=2048)),
    vol.Optional("language"): vol.Match(r"^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})?$"),
    vol.Optional("translate", default=False): cv.boolean,
    vol.Optional("expires_in", default=300): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
    vol.Optional("idempotency_key"): vol.All(cv.string, vol.Length(min=1, max=128)),
    vol.Optional("require_ack", default=False): cv.boolean,
    vol.Optional("ack_timeout", default=60): vol.All(vol.Coerce(int), vol.Range(min=1, max=3600)),
    vol.Optional("fallback_notify", default=False): cv.boolean,
    vol.Optional("voicemail", default=False): cv.boolean,
    vol.Optional("defer_until_present", default=False): cv.boolean,
}
SCHEMAS = {
    "catalog": vol.Schema({}),
    "setup": vol.Schema(
        {
            vol.Required("action"): vol.In(
                ("discover", "list", "plan", "get", "apply", "verify", "undo", "test", "confirm")
            ),
            vol.Optional("settings", default={}): dict,
            vol.Optional("id"): cv.string,
            vol.Optional("revision"): cv.string,
            vol.Optional("confirm", default=False): cv.boolean,
            vol.Optional("speaker"): cv.entity_id,
            vol.Optional("heard", default=False): cv.boolean,
            vol.Optional("media_only", default=False): cv.boolean,
        }
    ),
    "announcements": vol.Schema(
        {
            vol.Required("action"): vol.In(
                ("schema", "list", "create", "update", "enable", "disable", "delete")
            ),
            vol.Optional("id"): cv.string,
            vol.Optional("revision"): cv.string,
            vol.Optional("kind"): vol.In(("scheduled", "triggered")),
            vol.Optional("values", default={}): dict,
            vol.Optional("idempotency_key"): vol.All(cv.string, vol.Length(min=1, max=128)),
            vol.Optional("confirm", default=False): cv.boolean,
        }
    ),
    "doctor": vol.Schema(
        {
            vol.Optional("action", default="inspect"): vol.In(("inspect", "plan_route")),
            vol.Optional("speaker"): cv.entity_id,
            vol.Optional("alternative"): cv.entity_id,
        }
    ),
    "discover": vol.Schema({}),
    "diagnose": vol.Schema(TARGETS),
    "preview": vol.Schema(TARGETS),
    "send": vol.Schema(MESSAGE),
    "speech": vol.Schema(
        {**MESSAGE, vol.Optional("action", default="prepare"): vol.In(("prepare", "clear"))}
    ),
    "ask": vol.Schema(
        {
            vol.Required("satellites"): vol.All(cv.entity_ids, vol.Length(min=1, max=16)),
            vol.Required("message"): vol.All(cv.string, vol.Length(min=1, max=4000)),
            vol.Optional("answers", default=[]): [
                vol.Schema(
                    {
                        vol.Required("id"): cv.string,
                        vol.Required("sentences"): vol.All(cv.ensure_list, [cv.string]),
                    }
                )
            ],
            vol.Optional("expires_in", default=60): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=600)
            ),
            vol.Optional("idempotency_key"): vol.All(cv.string, vol.Length(min=1, max=128)),
        }
    ),
    "status": vol.Schema({vol.Required("id"): cv.string}),
    "cancel": vol.Schema({vol.Required("id"): cv.string}),
    "acknowledge": vol.Schema(
        {
            vol.Required("id"): cv.string,
            vol.Optional("response", default=""): vol.All(cv.string, vol.Length(max=500)),
        }
    ),
    "tasks": vol.Schema(
        {
            vol.Optional("action", default="list"): vol.In(("list", "complete", "snooze")),
            vol.Optional("id"): cv.string,
            vol.Optional("minutes", default=30): vol.All(
                vol.Coerce(int), vol.Range(min=1, max=1440)
            ),
        }
    ),
    "sound_test": vol.Schema({vol.Required("speaker"): cv.entity_id}),
    "confirm_test": vol.Schema({vol.Required("id"): cv.string, vol.Required("heard"): cv.boolean}),
    "adapter": vol.Schema(
        {
            vol.Required("adapter"): cv.string,
            vol.Required("action"): vol.In(("call", "answer", "hangup")),
        }
    ),
    "voicemail": vol.Schema(
        {
            vol.Optional("action", default="list"): vol.In(("list", "delete", "replay")),
            vol.Optional("id"): cv.string,
        }
    ),
}


async def permitted(
    hass: HomeAssistant,
    entity_id: str,
    context: Context,
    assistant: str | None = None,
    *,
    control: bool = True,
) -> bool:
    if assistant and not async_should_expose(hass, assistant, entity_id):
        return False
    if context.user_id:
        user = await hass.auth.async_get_user(context.user_id)
        return bool(
            user
            and user.permissions.check_entity(entity_id, POLICY_CONTROL if control else POLICY_READ)
        )
    return True


async def validate_targets(
    hass: HomeAssistant,
    values: dict[str, Any],
    context: Context,
    assistant: str | None,
) -> dict[str, Any]:
    data = hass.data[DOMAIN]
    if not any(values.get(key) for key in ("areas", "speakers", "people", "everywhere")):
        raise Problem("target_required")
    # Resolve an exact preview before any provider call or audio generation.
    request = CallRequest(
        targets=[*values.get("areas", []), *values.get("speakers", [])],
        people=values.get("people"),
        media_id="",
        kind="message",
        priority=values.get("priority", "normal"),
        volume=values.get("volume"),
    )
    prepared = prepare_call(hass, data, request)
    for entity_id in [*prepared.routing.entity_ids, *values.get("people", [])]:
        if not await permitted(hass, entity_id, context, assistant):
            raise Problem("forbidden")
    return {
        "ok": True,
        "targets": prepared.targets,
        "endpoints": prepared.routing.entity_ids,
        "volumes": prepared.routing.volumes,
        "skipped": prepared.routing.skipped,
        "audible": None,
        "delivery_semantics": "request_accepted",
    }


async def task_permitted(
    hass: HomeAssistant, task_id: str, context: Context, assistant: str | None = None
) -> bool:
    """Changing a task needs control of its Mark done button, wherever the request comes from."""
    from homeassistant.helpers import entity_registry as er

    button = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{task_id}_complete")
    return not button or await permitted(hass, button, context, assistant)


async def _tasks(
    hass: HomeAssistant, data, values: dict[str, Any], context: Context, assistant: str | None
) -> dict:
    """Announcement tasks the caller may see; changing one needs its Mark done button."""
    from homeassistant.helpers import entity_registry as er

    registry = er.async_get(hass)
    announcer = data.announcer
    if values["action"] != "list":
        task_id = values.get("id", "")
        if not await task_permitted(hass, task_id, context, assistant):
            raise Problem("forbidden")
        if values["action"] == "complete":
            await announcer.async_complete(task_id, context)
        else:
            await announcer.async_snooze(task_id, values["minutes"])
    tasks = []
    for sid, item in announcer.items.items():
        sensor = registry.async_get_entity_id("sensor", DOMAIN, f"{sid}_task")
        if (
            item.task
            and sensor
            and await permitted(hass, sensor, context, assistant, control=False)
        ):
            tasks.append({**item.task_summary(), "entity_id": sensor})
    return {"ok": True, "tasks": tasks}


async def execute(
    hass: HomeAssistant,
    operation: str,
    values: dict[str, Any],
    context: Context,
    *,
    assistant: str | None = None,
) -> dict[str, Any]:
    result = await _execute(hass, operation, values, context, assistant=assistant)
    return {**result, "api_version": API_VERSION}


async def _execute(hass, operation, values, context, *, assistant=None):
    try:
        if operation not in SCHEMAS:
            raise Problem("invalid_request")
        values = SCHEMAS[operation](values)
        if operation == "catalog":
            from .protocol import catalog

            return catalog(hass)
        if operation in {"setup", "announcements", "doctor"}:
            manager = hass.data.get(SETUP_KEY)
            if manager is None:
                raise Problem("not_ready", retryable=True)
            if operation == "setup":
                return await manager.execute(values, context, assistant)
            if operation == "doctor":
                return await manager.doctor(values, context, assistant)
            from .announcement_api import execute as manage_announcements

            return await manage_announcements(hass, values, context, assistant)
        if "message" in values:
            values["message"] = values["message"].strip()
            if not values["message"]:
                raise Problem("content_required")
        data = hass.data.get(DOMAIN)
        if data is None:
            raise Problem("not_ready", retryable=True)
        if operation == "speech":
            if values.pop("action") == "clear":
                from .onboarding import require_admin

                await require_admin(hass, context)
                data.speech_cache.clear()
                return {"ok": True}
            await validate_targets(hass, values, context, assistant)
            if not values.get("message") or values.get("media_url"):
                raise Problem("content_required")
            from .messages import request_for

            request = await request_for(hass, data, {**values, "_prepare": True}, context)
            return {"ok": True, "prepared": True, "bytes": len(request.audio), "audible": None}
        if operation == "tasks":
            return await _tasks(hass, data, values, context, assistant)
        if operation in ("discover", "diagnose"):
            from .shortcuts import room_choices

            result = discover(hass, data)
            result["speech_profiles"] = sorted(data.speech_profiles)
            result["shortcut_choices"] = await room_choices(
                hass, data, context, assistant=assistant
            )
            for key in ("endpoints", "satellites", "people"):
                result[key] = [
                    item
                    for item in result[key]
                    if await permitted(hass, item["entity_id"], context, assistant, control=False)
                ]
            if assistant:
                allowed_areas = {e["area_id"] for e in result["endpoints"]}
                result["rooms"] = [r for r in result["rooms"] if r["area_id"] in allowed_areas]
                result["areas"] = [a for a in result["areas"] if a["id"] in allowed_areas]
                result["adapters"] = []
                result["stations"] = []
            if operation == "diagnose":
                result["metrics"] = data.metrics.summary()
                result["policy"] = {
                    "dnd": data.dnd,
                    "quiet_hours": data.quiet.active(dt_util.now()),
                }
            return {"ok": True, **result}
        if operation in ("preview", "send"):
            if values.get("defer_until_present"):
                if not values.get("people"):
                    raise Problem("target_required")
                for person in values["people"]:
                    if not await permitted(hass, person, context, assistant):
                        raise Problem("forbidden")
            else:
                preview = await validate_targets(hass, values, context, assistant)
                if operation == "preview":
                    return preview
            if not bool(values.get("message")) != bool(values.get("media_url")):
                raise Problem("content_required")
            if values.get("voicemail") and not data.voicemail_enabled:
                raise Problem("voicemail_disabled")
            job = data.jobs.submit(values, context, assistant)
            if values.get("durable"):
                await data.jobs.save()
            return {"ok": True, **job.summary()}
        if operation == "ask":
            known = {
                e["entity_id"]
                for e in discover(hass, data)["satellites"]
                if e["can_ask"] and e["available"]
            }
            if not set(values["satellites"]) <= known:
                raise Problem("satellite_unavailable")
            for entity_id in values["satellites"]:
                if not await permitted(hass, entity_id, context, assistant):
                    raise Problem("forbidden")
            return {"ok": True, **data.jobs.submit(values, context, assistant).summary()}
        if operation in ("status", "cancel", "acknowledge"):
            job = await data.jobs.get(values["id"], context, recipient=operation == "acknowledge")
            if assistant:
                entities = [*job.values.get("people", []), *job.values.get("satellites", [])]
                if job.record:
                    entities.extend(result.entity_id for result in job.record.results)
                elif any(job.values.get(key) for key in ("areas", "speakers", "everywhere")):
                    entities.extend(
                        resolve(
                            hass,
                            data.speakers,
                            [*job.values.get("areas", []), *job.values.get("speakers", [])],
                        )
                    )
                for entity_id in entities:
                    if not await permitted(
                        hass, entity_id, context, assistant, control=operation != "status"
                    ):
                        raise Problem("forbidden")
            if operation == "cancel":
                job = await data.jobs.cancel(values["id"], context)
            elif operation == "acknowledge":
                job = await data.jobs.acknowledge(values["id"], context, values["response"])
            return {"ok": True, **job.summary()}
        if operation == "sound_test":
            speaker = values["speaker"]
            if speaker not in data.speakers:
                raise Problem("no_targets")
            if not await permitted(hass, speaker, context, assistant):
                raise Problem("forbidden")
            from .const import API_CHIME
            from .http import base_url

            job = data.jobs.submit(
                {
                    "speakers": [speaker],
                    "media_url": f"{base_url(hass)}{API_CHIME.format(style='ding')}.mp3",
                },
                context,
            )
            data.tests[job.id] = (speaker, time.monotonic(), context.user_id)
            while len(data.tests) > 128:
                data.tests.pop(next(iter(data.tests)))
            data.metrics.counters["sound_tests"] += 1
            return {"ok": True, **job.summary()}
        if operation == "confirm_test":
            test = data.tests.get(values["id"])
            if test is None or test[2] != context.user_id:
                raise Problem("not_found")
            job = await data.jobs.get(values["id"], context)
            if job.state not in {"completed", "failed", "partial"}:
                raise Problem("not_ready", retryable=True)
            data.tests.pop(values["id"])
            data.sound_tests[test[0]] = {"heard": values["heard"], "at": time.time()}
            await data.setup_store.async_save({"sound_tests": data.sound_tests})
            data.metrics.counters[
                "sound_tests_confirmed" if values["heard"] else "sound_tests_silent"
            ] += 1
            if values["heard"]:
                data.metrics.observe(
                    "setup_confirmation", (time.monotonic() - data.metrics.started) * 1000
                )
            elif job.record and job.record.ok:
                data.metrics.counters["accepted_but_silent"] += 1
            return {"ok": True, **data.sound_tests[test[0]]}
        if operation == "adapter":
            adapter = data.adapters.get(values["adapter"])
            if adapter is None:
                raise Problem("adapter_unavailable")
            if not await permitted(hass, adapter["script"], context, assistant):
                raise Problem("forbidden")
            await hass.services.async_call(
                "script",
                "turn_on",
                {
                    "entity_id": adapter["script"],
                    "variables": {
                        "roomcall_action": values["action"],
                        "roomcall_adapter": values["adapter"],
                    },
                },
                blocking=True,
                context=context,
            )
            return {"ok": True, "state": "request_accepted"}
        if operation == "voicemail":
            if not data.voicemail_enabled:
                raise Problem("voicemail_disabled")
            if values["action"] == "list":
                return {"ok": True, "messages": data.mailbox.list(context.user_id)}
            item = data.mailbox.items.get(values.get("id"))
            if item is None or item["owner"] != context.user_id or item["expires"] <= time.time():
                raise Problem("not_found")
            if values["action"] == "delete":
                await data.mailbox.delete(item["id"])
                return {"ok": True}
            params = {"message": item["message"], **item["request"]}
            if item.get("audio"):
                import base64

                from .http import base_url

                clip_id = data.clips.add(base64.b64decode(item["audio"]), "audio/mpeg")
                params.pop("message", None)
                params["media_url"] = f"{base_url(hass)}/api/roomcall/clip/{clip_id}.mp3"
            await validate_targets(hass, params, context, assistant)
            return {"ok": True, **data.jobs.submit(params, context).summary()}
        raise Problem("invalid_request")
    except vol.Invalid:
        return Problem("invalid_request").as_dict(hass)
    except CallError as err:
        return Problem(
            err.code,
            retryable=err.code in {"dnd_blocked", "quiet_hours_blocked", "presence_unknown"},
        ).as_dict(hass)
    except Problem as err:
        return err.as_dict(hass)
    except (TimeoutError, asyncio.CancelledError):
        raise
    except Exception:
        logging.getLogger(__name__).exception("RoomCall operation %s failed", operation)
        return Problem("provider_failed", retryable=True).as_dict(hass)
