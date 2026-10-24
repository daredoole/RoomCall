"""Verified device companions and explicit HDMI/audio-output routes; never room guesses."""

from __future__ import annotations

from typing import Any

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er


def is_music_assistant(hass: HomeAssistant, entity_id: str) -> bool:
    entry = er.async_get(hass).async_get(entity_id)
    return bool(entry and entry.platform == "music_assistant")


def identities(hass: HomeAssistant, entity_id: str) -> set[str]:
    runtime = hass.data.get("roomcall")
    aliases = getattr(runtime, "aliases", {})
    if entity_id in aliases:
        return {f"alias:{aliases[entity_id]}"}
    entry = er.async_get(hass).async_get(entity_id)
    device = dr.async_get(hass).async_get(entry.device_id) if entry and entry.device_id else None
    if device is None:
        return {f"entity:{entity_id}"}
    values = {f"device:{device.id}"}
    values.update(f"mac:{value.lower()}" for kind, value in device.connections if kind == "mac")
    serial = getattr(device, "serial_number", None)
    # HA's Denon AVR device identifier includes the serial, unlike its serial_number field.
    if not serial and device.model:
        prefix = f"{device.model}-"
        serial = next(
            (
                value[len(prefix) :]
                for kind, value in device.identifiers
                if kind == "denonavr" and value.startswith(prefix)
            ),
            None,
        )
    if (
        serial
        and serial.strip("0-: ")
        and device.manufacturer
        and len(serial) >= 4
        and serial.casefold() not in {"unknown", "none", "n/a", "00000000"}
    ):
        maker = device.manufacturer.casefold()
        model = (device.model or "").casefold().removeprefix(f"{maker} ")
        values.add(f"serial:{maker}:{model}:{serial.casefold()}")
    return values


def related(hass: HomeAssistant, entity_id: str, *, available_only: bool = True) -> list[str]:
    registry = er.async_get(hass)
    identity = identities(hass, entity_id)
    result = [entity_id]
    for other in hass.states.async_entity_ids("media_player"):
        entry = registry.async_get(other)
        if (
            other != entity_id
            and entry
            and not entry.disabled_by
            # MA owns its announcement restoration; never restore it as a native companion.
            and entry.platform not in {"music_assistant", "dlna_dmr"}
            and not entry.unique_id.endswith(("-Zone2", "-Zone3"))
            and (
                not available_only
                or hass.states.get(other).state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            )
            and identity.intersection(identities(hass, other))
        ):
            result.append(other)
    return result


def route(hass: HomeAssistant, entity_id: str) -> dict[str, str]:
    runtime = hass.data.get("roomcall")
    explicit = getattr(runtime, "playback_routes", {}).get(entity_id)
    if explicit:
        return dict(explicit)
    registry = er.async_get(hass)
    entry = registry.async_get(entity_id)
    if entry and entry.platform == "heos":
        candidates = [
            other
            for other in related(hass, entity_id, available_only=False)
            if other != entity_id and registry.async_get(other).platform == "denonavr"
        ]
        if len(candidates) == 1:
            return {"controller": candidates[0], "source": "HEOS Music"}
        if candidates:
            return {"error": "playback_controller_ambiguous"}
    return {}


def controlled_entities(hass: HomeAssistant, entity_id: str) -> list[str]:
    from homeassistant.components.media_player import MediaPlayerEntityFeature as F

    output = route(hass, entity_id).get("controller")
    state = hass.states.get(entity_id)
    if not output and (
        not getattr(hass.data.get("roomcall"), "restore", False)
        or (state and state.attributes.get("supported_features", 0) & F.MEDIA_ANNOUNCE)
    ):
        return [entity_id]
    return list(dict.fromkeys([*related(hass, entity_id), *([output] if output else [])]))


def describe(hass: HomeAssistant, entity_id: str) -> dict[str, Any]:
    from homeassistant.components.media_player import MediaPlayerEntityFeature as F

    registry = er.async_get(hass)
    entries = controlled_entities(hass, entity_id)
    android = [
        e
        for e in entries
        if registry.async_get(e) and registry.async_get(e).platform == "androidtv_remote"
    ]
    state = hass.states.get(entity_id)
    features = state.attributes.get("supported_features", 0) if state else 0
    output = route(hass, entity_id)
    controller = hass.states.get(output.get("controller", entity_id))
    return {
        **output,
        "source_options": list(controller.attributes.get("source_list") or [])
        if controller
        else [],
        "entities": entries,
        "restore_enabled": getattr(hass.data.get("roomcall"), "restore", False),
        "android_app_reopen": bool(android),
        "media_resume": bool(
            registry.async_get(entity_id)
            and registry.async_get(entity_id).platform == "cast"
            and features & F.PLAY_MEDIA
        ),
        "position_resume": bool(
            registry.async_get(entity_id)
            and registry.async_get(entity_id).platform == "cast"
            and features & F.SEEK
        ),
        "resume_semantics": "best_effort_not_exact_session",
    }
