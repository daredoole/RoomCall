"""Conservative endpoint identity, capability discovery and inspectable routing."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .core import friendly, rooms, speaker_area, supports_announce

if TYPE_CHECKING:
    from . import RoomCallData


def physical_key(hass: HomeAssistant, entity_id: str, aliases: dict[str, str]) -> str:
    """Use explicit aliases, shared registry devices, or shared stable connections."""
    if entity_id in aliases:
        return f"alias:{aliases[entity_id]}"
    entry = er.async_get(hass).async_get(entity_id)
    if entry and entry.device_id:
        device = dr.async_get(hass).async_get(entry.device_id)
        if device and device.connections:
            # Network hardware addresses can link entries created by different integrations.
            macs = sorted(value.lower() for kind, value in device.connections if kind == "mac")
            if macs:
                return f"mac:{macs[0]}"
        return f"device:{entry.device_id}"
    return f"entity:{entity_id}"


def deduplicate(
    hass: HomeAssistant, entity_ids: list[str], aliases: dict[str, str]
) -> tuple[list[str], dict[str, str]]:
    selected: dict[str, str] = {}
    skipped = {}
    registry = er.async_get(hass)

    def rank(entity_id: str) -> tuple[bool, bool]:
        state = hass.states.get(entity_id)
        return (
            state is not None and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN),
            supports_announce(hass, entity_id),
        )

    for entity_id in dict.fromkeys(entity_ids):
        entry = registry.async_get(entity_id)
        if entry and entry.disabled_by:
            skipped[entity_id] = "disabled"
            continue
        key = physical_key(hass, entity_id, aliases)
        previous = selected.get(key)
        if previous is None:
            selected[key] = entity_id
        elif rank(entity_id) > rank(previous):
            skipped[previous] = f"duplicate:{entity_id}"
            selected[key] = entity_id
        else:
            skipped[entity_id] = f"duplicate:{previous}"
    return list(selected.values()), skipped


def discover(hass: HomeAssistant, data: RoomCallData) -> dict[str, Any]:
    from .policy import person_area

    selected, skipped = deduplicate(hass, data.speakers, data.aliases)
    registry = er.async_get(hass)
    endpoints = []
    for entity_id in data.speakers:
        state = hass.states.get(entity_id)
        entry = registry.async_get(entity_id)
        endpoints.append(
            {
                "entity_id": entity_id,
                "name": friendly(hass, entity_id),
                "area_id": speaker_area(hass, entity_id),
                "physical_id": physical_key(hass, entity_id, data.aliases),
                "route": entry.platform if entry else "media_player",
                "available": state is not None
                and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN),
                "selected": entity_id in selected,
                "skip_reason": skipped.get(entity_id),
                "announce": supports_announce(hass, entity_id),
                "playback_observable": False,
                "microphone": False,
                "sound_test": data.sound_tests.get(entity_id),
            }
        )
    satellites = []
    for state in hass.states.async_all("assist_satellite"):
        features = state.attributes.get("supported_features", 0)
        satellites.append(
            {
                "entity_id": state.entity_id,
                "name": state.name,
                "available": state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN),
                "can_ask": bool(features & 2)
                and hass.services.has_service("assist_satellite", "ask_question"),
                "area_id": speaker_area(hass, state.entity_id),
            }
        )
    return {
        "schema_version": 1,
        "rooms": rooms(hass, selected),
        "endpoints": endpoints,
        "satellites": satellites,
        "stations": data.stations.list() if data.stations else [],
        "adapters": [
            {
                "id": key,
                "kind": value["kind"],
                "available": hass.states.get(value["script"]) is not None,
            }
            for key, value in data.adapters.items()
        ],
        "areas": [{"id": a.id, "name": a.name} for a in ar.async_get(hass).async_list_areas()],
        "people": [
            {
                "entity_id": state.entity_id,
                "name": state.name,
                "area_id": person_area(
                    hass,
                    state.entity_id,
                    max_age=data.presence_max_age,
                    min_confidence=data.presence_min_confidence,
                ),
                "preferred_language": data.preferences.get(state.entity_id, {}).get("language"),
            }
            for state in hass.states.async_all("person")
        ],
        "features": {
            "jobs": True,
            "preview": True,
            "voicemail": data.voicemail_enabled,
            "translation": bool(data.translation_entity),
            "audio_processing": data.audio_processing,
        },
    }


def can_play(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    return bool(
        state
        and state.attributes.get("supported_features", 0) & MediaPlayerEntityFeature.PLAY_MEDIA
    )
