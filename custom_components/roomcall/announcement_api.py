"""Dedicated, revision-checked announcement administration via native HA flows."""

from __future__ import annotations

import copy

from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .errors import Problem
from .onboarding import current_entry, fingerprint, require_admin
from .protocol import SETUP_KEY, json_schema
from .schema import vol


def summary(hass: HomeAssistant, subentry) -> dict:
    runtime = hass.data.get(DOMAIN)
    item = runtime.announcer.items.get(subentry.subentry_id) if runtime else None
    result = {
        "id": subentry.subentry_id,
        "kind": subentry.subentry_type,
        "values": {"name": subentry.title, **dict(subentry.data)},
        "enabled": item.enabled if item else None,
    }
    return {**result, "revision": fingerprint(result)}


async def execute(
    hass: HomeAssistant, values: dict, context: Context, assistant: str | None
) -> dict:
    from .communication import permitted
    from .config_flow import ScheduledAnnouncementFlow, TriggeredAnnouncementFlow

    await require_admin(hass, context)
    manager = hass.data[SETUP_KEY]
    async with manager.lock:
        action = values["action"]
        classes = {"scheduled": ScheduledAnnouncementFlow, "triggered": TriggeredAnnouncementFlow}
        if action == "schema":
            return {
                "ok": True,
                "schemas": {name: json_schema(cls()._schema({})) for name, cls in classes.items()},
            }
        entry = current_entry(hass)
        if entry is None:
            raise Problem("not_ready")
        if action == "list":
            return {
                "ok": True,
                "announcements": [
                    summary(hass, sub)
                    for sub in entry.subentries.values()
                    if sub.subentry_type in classes
                ],
            }
        subentry = entry.subentries.get(values.get("id", ""))
        if action != "create":
            if subentry is None or subentry.subentry_type not in classes:
                raise Problem("not_found")
            if values.get("revision") != summary(hass, subentry)["revision"]:
                raise Problem("configuration_conflict")
        if action == "delete":
            if not values.get("confirm"):
                raise Problem("confirmation_required")
            hass.config_entries.async_remove_subentry(entry, subentry.subentry_id)
            return {"ok": True, "deleted": subentry.subentry_id}
        if action in {"enable", "disable"}:
            entity_id = er.async_get(hass).async_get_entity_id(
                "switch", DOMAIN, f"{subentry.subentry_id}_enabled"
            )
            if not entity_id:
                raise Problem("not_ready", retryable=True)
            await hass.services.async_call(
                "switch",
                "turn_on" if action == "enable" else "turn_off",
                {"entity_id": entity_id},
                blocking=True,
                context=context,
            )
            return {"ok": True, "announcement": summary(hass, subentry)}
        kind = subentry.subentry_type if subentry else values.get("kind")
        if kind not in classes:
            raise Problem("invalid_request")
        proposed = {"name": subentry.title, **dict(subentry.data)} if subentry else {}
        proposed.update(values.get("values", {}))
        flow = classes[kind]()
        try:
            proposed = flow._schema(proposed)(proposed)
            errors = flow._validate(proposed)
            if errors or not proposed["name"].strip() or len(proposed["message"]) > 4000:
                raise Problem("invalid_settings", fields=errors)
        except vol.Invalid as err:
            raise Problem("invalid_settings", fields=[str(p) for p in err.path]) from err
        for area in proposed.get("rooms", []):
            if ar.async_get(hass).async_get_area(area) is None:
                raise Problem("no_targets", area_id=area)
        for entity_id in [
            *proposed.get("target_speakers", []),
            *proposed.get("people", []),
            *([proposed["entity"]] if proposed.get("entity") else []),
        ]:
            if hass.states.get(entity_id) is None:
                raise Problem("no_targets", entity_id=entity_id)
            if not await permitted(hass, entity_id, context, assistant):
                raise Problem("forbidden")
        if action == "create":
            digest = fingerprint({"kind": kind, "values": proposed})
            key = fingerprint([context.user_id, values.get("idempotency_key") or digest])
            existing = manager.announcement_keys.get(key)
            if existing and existing["digest"] != digest:
                raise Problem("idempotency_conflict")
            if existing and not entry.subentries.get(existing["id"]):
                raise Problem("not_found")
            if existing:
                return {
                    "ok": True,
                    "announcement": summary(hass, entry.subentries[existing["id"]]),
                    "reused": True,
                }
            sub = next(
                (
                    sub
                    for sub in entry.subentries.values()
                    if sub.subentry_type == kind
                    and sub.title == proposed["name"]
                    and dict(sub.data) == {k: v for k, v in proposed.items() if k != "name"}
                ),
                None,
            )
            if sub:
                manager.announcement_keys[key] = {"digest": digest, "id": sub.subentry_id}
                while len(manager.announcement_keys) > 128:
                    manager.announcement_keys.pop(next(iter(manager.announcement_keys)))
                await manager.save()
                return {"ok": True, "announcement": summary(hass, sub), "reused": True}
            before = set(entry.subentries)
            result = await hass.config_entries.subentries.async_init(
                (entry.entry_id, kind), context={"source": "user"}, data=copy.deepcopy(proposed)
            )
            if result["type"] != FlowResultType.CREATE_ENTRY:
                raise Problem("invalid_settings", fields=result.get("errors", {}))
            subentry = next(
                sub
                for sid, sub in entry.subentries.items()
                if sid not in before and sub.title == proposed["name"]
            )
            manager.announcement_keys[key] = {"digest": digest, "id": subentry.subentry_id}
            while len(manager.announcement_keys) > 128:
                manager.announcement_keys.pop(next(iter(manager.announcement_keys)))
            await manager.save()
        else:
            result = await entry.start_subentry_reconfigure_flow(hass, subentry.subentry_id)
            result = await hass.config_entries.subentries.async_configure(
                result["flow_id"], copy.deepcopy(proposed)
            )
            if result.get("reason") != "reconfigure_successful":
                raise Problem("invalid_settings", fields=result.get("errors", {}))
        return {"ok": True, "announcement": summary(hass, subentry)}
