"""Persistent administrator-owned plans applied through Home Assistant config flows."""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import secrets
import time
from typing import Any

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.storage import Store

from .capabilities import deduplicate
from .const import DOMAIN
from .core import friendly, speaker_area
from .errors import Problem
from .localization import text
from .schema import vol

SETTINGS = {
    "speakers",
    "volume",
    "gain_db",
    "tts_engine",
    "chime_options",
    "delivery",
    "quiet_hours",
    "replies",
    "homekit",
    "communication",
}


def fingerprint(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def merged(base: dict, patch: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = merged(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


async def require_admin(hass: HomeAssistant, context: Context) -> None:
    user = await hass.auth.async_get_user(context.user_id) if context.user_id else None
    if user is None or not user.is_admin:
        raise Problem("admin_required")


def current_entry(hass: HomeAssistant):
    return next(iter(hass.config_entries.async_entries(DOMAIN)), None)


def revision(entry) -> str:
    return (
        fingerprint(
            {"id": entry.entry_id, "data": dict(entry.data), "options": dict(entry.options)}
        )
        if entry
        else fingerprint(None)
    )


class Setup:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = Store(hass, 1, "roomcall.setup_plans")
        self.plans: dict[str, dict[str, Any]] = {}
        self.announcement_keys: dict[str, dict] = {}
        self.lock = asyncio.Lock()

    async def load(self) -> None:
        loaded = await self.store.async_load() or {}
        self.plans = loaded.get("plans", {})
        self.announcement_keys = loaded.get("announcement_keys", {})

    async def save(self) -> None:
        await self.store.async_save(
            {"plans": self.plans, "announcement_keys": self.announcement_keys}
        )

    async def discover(self, context: Context, assistant: str | None) -> dict:
        from .communication import permitted
        from .config_flow import _options_schema
        from .protocol import json_schema

        entry = current_entry(self.hass)
        configured = {**entry.data, **entry.options} if entry else {}
        registry = er.async_get(self.hass)
        endpoints = []
        candidates = []
        for state in self.hass.states.async_all("media_player"):
            item = registry.async_get(state.entity_id)
            if item and item.disabled_by:
                continue
            if not await permitted(self.hass, state.entity_id, context, assistant):
                continue
            features = state.attributes.get("supported_features", 0)
            eligible = bool(features & (512 | 1048576)) and not (
                item and item.platform in {"music_assistant", "dlna_dmr", "mobile_app"}
            )
            if eligible:
                candidates.append(state.entity_id)
            endpoints.append(
                {
                    "entity_id": state.entity_id,
                    "name": state.name,
                    "area_id": speaker_area(self.hass, state.entity_id),
                    "available": state.state not in {"unknown", "unavailable"},
                    "eligible": eligible,
                    "selected": state.entity_id in configured.get("speakers", []),
                }
            )
        aliases = configured.get("communication", {}).get("aliases", {})
        for entity_id in configured.get("speakers", []):
            if self.hass.states.get(entity_id) is None and await permitted(
                self.hass, entity_id, context, assistant
            ):
                endpoints.append(
                    {
                        "entity_id": entity_id,
                        "name": friendly(self.hass, entity_id),
                        "area_id": speaker_area(self.hass, entity_id),
                        "available": False,
                        "eligible": False,
                        "selected": True,
                    }
                )
        recommended, duplicates = deduplicate(self.hass, candidates, aliases)
        engines = [
            e
            for e in self.hass.states.async_entity_ids("tts")
            if self.hass.states.get(e).state != "unavailable"
            and not ((item := registry.async_get(e)) and item.disabled_by)
            and await permitted(self.hass, e, context, assistant)
        ]
        questions = []
        if not recommended:
            questions.append(
                {
                    "code": "no_targets",
                    "field": "speakers",
                    "message": text(self.hass, "no_targets"),
                }
            )
        if not configured.get("tts_engine") and len(engines) != 1:
            questions.append(
                {
                    "code": "choose_tts",
                    "field": "tts_engine",
                    "choices": engines,
                    "message": text(self.hass, "choose_tts"),
                }
            )
        return {
            "configured": entry is not None,
            "entry_id": entry.entry_id if entry else None,
            "endpoints": endpoints,
            "tts_engines": [{"entity_id": e, "name": friendly(self.hass, e)} for e in engines],
            "areas": [
                {"id": a.id, "name": a.name} for a in ar.async_get(self.hass).async_list_areas()
            ],
            "recommended": {
                "speakers": configured.get("speakers", recommended),
                **(
                    {"tts_engine": configured.get("tts_engine", engines[0])}
                    if len(engines) == 1
                    else {}
                ),
            },
            "duplicates": duplicates,
            "questions": questions,
            "settings": {k: v for k, v in configured.items() if k in SETTINGS},
            "settings_schema": json_schema(_options_schema(self.hass, configured)),
        }

    async def get(self, plan_id: str, context: Context) -> dict:
        plan = self.plans.get(plan_id)
        if plan is None or plan["owner"] != context.user_id:
            raise Problem("not_found")
        if plan["expires"] <= time.time():
            raise Problem("plan_expired")
        return plan

    def public(self, plan: dict) -> dict:
        return {
            k: copy.deepcopy(v)
            for k, v in plan.items()
            if k not in {"owner", "before_options", "before_revision", "after_revision"}
        }

    async def plan(self, values: dict, context: Context, assistant: str | None) -> dict:
        from .communication import permitted
        from .config_flow import _options_schema, validate_communication

        if len(self.plans) >= 32:
            disposable = next(
                (
                    p
                    for p in self.plans.values()
                    if p["expires"] <= time.time() or p["state"] in {"draft", "rolled_back"}
                ),
                None,
            )
            if disposable is None:
                raise Problem("queue_full")
            self.plans.pop(disposable["id"])
        patch = values.get("settings", {})
        if set(patch) - SETTINGS:
            raise Problem("invalid_settings", fields=sorted(set(patch) - SETTINGS))
        found = await self.discover(context, assistant)
        entry = current_entry(self.hass)
        base = {**entry.data, **entry.options} if entry else found["recommended"]
        desired = merged(base, patch)
        # Native options overlay initial entry data. Removing an override must
        # show the effective fallback in the review instead of promising an unset.
        if entry:
            for key, value in patch.items():
                if value is None and key in entry.data:
                    desired[key] = copy.deepcopy(entry.data[key])
        if (
            not desired.get("tts_engine")
            and "tts_engine" not in patch
            and len(found["tts_engines"]) == 1
        ):
            desired["tts_engine"] = found["tts_engines"][0]["entity_id"]
        if not desired.get("speakers"):
            raise Problem("no_targets")
        for entity in [
            *desired["speakers"],
            *([desired["tts_engine"]] if desired.get("tts_engine") else []),
        ]:
            if self.hass.states.get(entity) is None:
                raise Problem("no_targets", entity_id=entity)
            if not await permitted(self.hass, entity, context, assistant):
                raise Problem("forbidden")
        desired["speakers"], _ = deduplicate(
            self.hass, desired["speakers"], desired.get("communication", {}).get("aliases", {})
        )
        if not desired["speakers"]:
            raise Problem("no_targets")
        try:
            validated = _options_schema(self.hass, desired)(
                {k: v for k, v in desired.items() if k in SETTINGS}
            )
            validate_communication(validated.get("communication", {}))
        except vol.Invalid as err:
            raise Problem("invalid_settings", fields=[str(p) for p in err.path]) from err
        plan = {
            "id": secrets.token_urlsafe(16),
            "owner": context.user_id,
            "entry_id": entry.entry_id if entry else None,
            "created_entry": entry is None,
            "before_options": dict(entry.options) if entry else {},
            "before_revision": revision(entry),
            "settings": validated,
            "state": "draft",
            "created": time.time(),
            "expires": time.time() + 86400,
            "tests": {},
            "media_only": values.get("media_only", False),
            "questions": []
            if validated.get("tts_engine") or values.get("media_only")
            else [
                {
                    "code": "choose_tts",
                    "field": "tts_engine",
                    "message": text(self.hass, "choose_tts"),
                }
            ],
            "changes": [
                {"field": k, "before": base.get(k), "after": validated.get(k)}
                for k in sorted((base.keys() | validated.keys()) & SETTINGS)
                if base.get(k) != validated.get(k)
            ],
        }
        plan["revision"] = fingerprint(
            {"id": plan["id"], "before": plan["before_revision"], "settings": validated}
        )
        self.plans[plan["id"]] = plan
        await self.save()
        return plan

    async def apply(self, plan: dict, supplied: str) -> None:
        if supplied != plan["revision"]:
            raise Problem("configuration_conflict")
        if plan["questions"]:
            raise Problem("choose_tts")
        entry = current_entry(self.hass)
        if (
            plan["state"] == "applying"
            and entry
            and (
                (plan["created_entry"] and dict(entry.data) == plan["settings"])
                or (entry.entry_id == plan["entry_id"] and dict(entry.options) == plan["settings"])
            )
        ):
            plan.update(entry_id=entry.entry_id, state="applied", after_revision=revision(entry))
            await self.save()
        if plan["state"] != "draft":
            if plan["state"] == "applied" and revision(entry) == plan["after_revision"]:
                return
            if plan["state"] != "applying":
                raise Problem("configuration_conflict")
        if revision(entry) != plan["before_revision"]:
            raise Problem("configuration_conflict")
        plan.update(state="applying", applied_at=time.time())
        await self.save()
        if revision(current_entry(self.hass)) != plan["before_revision"]:
            raise Problem("configuration_conflict")
        if entry:
            result = await self.hass.config_entries.options.async_init(
                entry.entry_id, data=copy.deepcopy(plan["settings"])
            )
        else:
            result = await self.hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}, data=copy.deepcopy(plan["settings"])
            )
        if result["type"] != FlowResultType.CREATE_ENTRY:
            plan["state"] = "draft"
            await self.save()
            raise Problem("invalid_settings", fields=result.get("errors", {}))
        entry = current_entry(self.hass)
        plan.update(
            entry_id=entry.entry_id,
            after_revision=revision(entry),
            state="applied",
            applied_at=time.time(),
        )
        await self.save()

    async def undo(self, plan: dict, supplied: str, confirm: bool) -> None:
        if not confirm:
            raise Problem("confirmation_required")
        if supplied != plan["revision"]:
            raise Problem("configuration_conflict")
        if plan["state"] == "rolled_back":
            return
        entry = current_entry(self.hass)
        if plan["state"] == "undoing" and (
            (plan["created_entry"] and entry is None)
            or (
                entry
                and entry.entry_id == plan["entry_id"]
                and dict(entry.options) == plan["before_options"]
            )
        ):
            plan["state"] = "rolled_back"
            await self.save()
            return
        if plan["state"] not in {"applied", "undoing"} or revision(entry) != plan["after_revision"]:
            raise Problem("configuration_conflict")
        if plan["created_entry"] and entry.subentries:
            raise Problem("configuration_conflict")
        plan["state"] = "undoing"
        await self.save()
        if revision(current_entry(self.hass)) != plan["after_revision"]:
            raise Problem("configuration_conflict")
        if plan["created_entry"]:
            await self.hass.config_entries.async_remove(entry.entry_id)
        else:
            # HA's public options setter restores the exact mapping, including an
            # empty mapping; the existing update listener handles the reload.
            self.hass.config_entries.async_update_entry(entry, options=plan["before_options"])
        plan["state"] = "rolled_back"
        await self.save()

    async def verify(self, plan: dict) -> dict:
        entry = current_entry(self.hass)
        data = self.hass.data.get(DOMAIN)
        matches = plan["state"] == "applied" and revision(entry) == plan.get("after_revision")
        ready = bool(
            matches
            and data
            and entry.state == ConfigEntryState.LOADED
            and set(data.speakers) == set(plan["settings"]["speakers"])
        )
        endpoints = []
        for entity_id in plan["settings"]["speakers"]:
            state = self.hass.states.get(entity_id)
            test = plan.get("confirmations", {}).get(entity_id, {})
            heard = test.get("heard") is True and test.get("at", 0) >= plan.get(
                "applied_at", float("inf")
            )
            endpoints.append(
                {
                    "entity_id": entity_id,
                    "available": state is not None
                    and state.state not in {"unknown", "unavailable"},
                    "heard": heard,
                }
            )
        audio_confirmed = bool(endpoints and all(e["heard"] for e in endpoints))
        engine = plan["settings"].get("tts_engine")
        tts_ready = bool(
            plan["media_only"]
            or (
                engine
                and (state := self.hass.states.get(engine)) is not None
                and state.state != "unavailable"
            )
        )
        configuration_ready = ready and tts_ready and all(e["available"] for e in endpoints)
        completed = configuration_ready and audio_confirmed
        if completed and not plan.get("completed_at"):
            plan["completed_at"] = time.time()
            data.metrics.counters["setup_sessions_completed"] += 1
            data.metrics.observe("setup_session", (time.time() - plan["created"]) * 1000)
            await self.save()
        next_actions = [{"operation": "setup", "request": {"action": "discover"}}]
        if configuration_ready:
            next_actions = [
                {
                    "operation": "setup",
                    "request": {"action": "test", "id": plan["id"], "speaker": e["entity_id"]},
                }
                for e in endpoints
                if not e["heard"]
            ]
        return {
            "configuration_ready": configuration_ready,
            "audio_confirmed": audio_confirmed,
            "completed": completed,
            "endpoints": endpoints,
            "tts_ready": tts_ready,
            "configuration_current": matches,
            "next_actions": next_actions,
        }

    async def execute(self, values: dict, context: Context, assistant: str | None) -> dict:
        from .communication import execute

        await require_admin(self.hass, context)
        async with self.lock:
            action = values["action"]
            if action == "discover":
                return {"ok": True, "discovery": await self.discover(context, assistant)}
            if action == "list":
                return {
                    "ok": True,
                    "plans": [
                        self.public(p)
                        for p in self.plans.values()
                        if p["owner"] == context.user_id and p["expires"] > time.time()
                    ],
                }
            if action == "plan":
                plan = await self.plan(values, context, assistant)
            else:
                plan = await self.get(values.get("id", ""), context)
                if action == "apply":
                    from .communication import permitted

                    for entity_id in [
                        *plan["settings"]["speakers"],
                        *(
                            [plan["settings"]["tts_engine"]]
                            if plan["settings"].get("tts_engine")
                            else []
                        ),
                    ]:
                        if self.hass.states.get(entity_id) is None:
                            raise Problem("no_targets", entity_id=entity_id)
                        if not await permitted(self.hass, entity_id, context, assistant):
                            raise Problem("forbidden")
                    await self.apply(plan, values.get("revision", ""))
                elif action == "undo":
                    await self.undo(plan, values.get("revision", ""), values.get("confirm", False))
                elif action in {"test", "confirm"}:
                    if (
                        plan["state"] != "applied"
                        or revision(current_entry(self.hass)) != plan["after_revision"]
                    ):
                        raise Problem("configuration_conflict")
                    speaker = values.get("speaker")
                    if speaker not in plan["settings"]["speakers"]:
                        raise Problem("no_targets")
                    if action == "test":
                        result = await execute(
                            self.hass,
                            "sound_test",
                            {"speaker": speaker},
                            context,
                            assistant=assistant,
                        )
                        if result["ok"]:
                            plan["tests"][speaker] = result["id"]
                            await self.save()
                    else:
                        result = await execute(
                            self.hass,
                            "confirm_test",
                            {
                                "id": plan["tests"].get(speaker, ""),
                                "heard": values.get("heard", False),
                            },
                            context,
                            assistant=assistant,
                        )
                        if result["ok"]:
                            plan.setdefault("confirmations", {})[speaker] = {
                                "heard": result["heard"],
                                "at": result["at"],
                            }
                            await self.save()
                    return {**result, "plan": self.public(plan)}
            result = {"ok": True, "plan": self.public(plan)}
            if action == "verify":
                result["verification"] = await self.verify(plan)
                result["plan"] = self.public(plan)
            return result

    async def doctor(self, values: dict, context: Context, assistant: str | None) -> dict:
        from homeassistant.util import dt as dt_util

        from .capabilities import physical_key
        from .http import base_url

        await require_admin(self.hass, context)
        found = await self.discover(context, assistant)
        data = self.hass.data.get(DOMAIN)
        findings = []
        alternatives = []
        for endpoint in found["endpoints"]:
            if endpoint["selected"] and not endpoint["available"]:
                findings.append(
                    {
                        "code": "endpoint_unavailable",
                        "entity_id": endpoint["entity_id"],
                        "message": text(self.hass, "endpoint_unavailable"),
                        "next_actions": [{"operation": "setup", "request": {"action": "discover"}}],
                    }
                )
                for candidate in found["endpoints"]:
                    aliases = data.aliases if data else {}
                    if (
                        candidate["available"]
                        and candidate["eligible"]
                        and candidate["entity_id"] != endpoint["entity_id"]
                        and physical_key(self.hass, candidate["entity_id"], aliases)
                        == physical_key(self.hass, endpoint["entity_id"], aliases)
                    ):
                        alternatives.append(
                            {"from": endpoint["entity_id"], "to": candidate["entity_id"]}
                        )
        if data and data.dnd:
            findings.append(
                {
                    "code": "dnd_blocked",
                    "message": text(self.hass, "dnd_blocked"),
                    "next_actions": [{"operation": "diagnose", "request": {}}],
                }
            )
        if data and data.quiet.active(dt_util.now()):
            findings.append(
                {
                    "code": "quiet_hours_active",
                    "message": text(self.hass, "quiet_hours_active"),
                    "next_actions": [{"operation": "diagnose", "request": {}}],
                }
            )
        if data:
            for entity_id, test in data.sound_tests.items():
                if not test["heard"] and entity_id in {e["entity_id"] for e in found["endpoints"]}:
                    findings.append(
                        {
                            "code": "sound_test_silent",
                            "entity_id": entity_id,
                            "message": text(self.hass, "sound_test_silent"),
                            "next_actions": [
                                {"operation": "sound_test", "request": {"speaker": entity_id}}
                            ],
                        }
                    )
            for record in data.history.records()[:1]:
                for entity_id, evidence in record.evidence.items():
                    if (
                        evidence.get("request_accepted")
                        and "playback_reported_ms" not in evidence
                        and entity_id in {e["entity_id"] for e in found["endpoints"]}
                    ):
                        findings.append(
                            {
                                "code": "audibility_unconfirmed",
                                "entity_id": entity_id,
                                "message": text(self.hass, "audibility_unconfirmed"),
                                "audio_fetched": "audio_fetched_ms" in evidence,
                                "next_actions": [
                                    {"operation": "sound_test", "request": {"speaker": entity_id}}
                                ],
                            }
                        )
        engine = found["settings"].get("tts_engine")
        if (engine and engine not in {e["entity_id"] for e in found["tts_engines"]}) or (
            not engine and len(found["tts_engines"]) != 1
        ):
            findings.append(
                {
                    "code": "choose_tts",
                    "message": text(self.hass, "choose_tts"),
                    "next_actions": [{"operation": "setup", "request": {"action": "discover"}}],
                }
            )
        try:
            url = base_url(self.hass)
            if url.startswith("https:"):
                findings.append(
                    {
                        "code": "speaker_url_tls",
                        "message": text(self.hass, "speaker_url_tls"),
                        "settings_path": "/config/network",
                    }
                )
        except NoURLAvailableError:
            findings.append(
                {
                    "code": "speaker_url_missing",
                    "message": text(self.hass, "speaker_url_missing"),
                    "settings_path": "/config/network",
                }
            )
        if values.get("action") == "plan_route":
            requested = {"from": values.get("speaker"), "to": values.get("alternative")}
            if requested not in alternatives:
                raise Problem("unsafe_repair")
            speakers = [
                requested["to"] if e == requested["from"] else e
                for e in found["settings"]["speakers"]
            ]
            return await self.execute(
                {"action": "plan", "settings": {"speakers": speakers}}, context, assistant
            )
        return {
            "ok": True,
            "findings": findings,
            "alternatives": alternatives,
            "audible": None,
            "metrics": data.metrics.summary() if data else {},
        }
