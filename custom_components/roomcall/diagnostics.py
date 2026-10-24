"""Diagnostics download: what RoomCall sees, with private details removed."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN, VERSION
from .core import rooms, speaker_area, supports_announce
from .protocol import SETUP_KEY

# Transcripts and messages are what people said; notify targets name their phones.
TO_REDACT = {"text", "message", "transcript", "notify", "custom_url", "pincode"}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    data = hass.data.get(DOMAIN)
    speakers = []
    for entity_id in data.speakers if data else []:
        state = hass.states.get(entity_id)
        speakers.append(
            {
                "entity_id": entity_id,
                "state": state.state if state else "missing",
                "area": speaker_area(hass, entity_id),
                "announce": supports_announce(hass, entity_id),
                "supported_features": state.attributes.get("supported_features") if state else None,
            }
        )
    homekit = None
    if data and data.homekit is not None:
        homekit = {
            "rooms": sorted(data.homekit.drivers),
            "paired": {
                area: bool(driver.state.paired) for area, driver in data.homekit.drivers.items()
            },
            "first_port": data.homekit.port,
        }
    return {
        "version": VERSION,
        "options": async_redact_data({**entry.data, **entry.options}, TO_REDACT),
        "runtime": None
        if data is None
        else {
            "chime": data.chime_style,
            "custom_chime": bool(data.chime_url),
            "stagger_ms": data.stagger_ms,
            "retry": data.retry,
            "restore": data.restore,
            "do_not_disturb": data.dnd,
            "quiet_hours_active": data.quiet.start is not None,
            "speech_to_text": bool(data.stt_engine),
            "notify_targets": len(data.notify),
        },
        "rooms": [
            {"area_id": r["area_id"], "name": r["name"], "speakers": len(r["speakers"])}
            for r in (rooms(hass, data.speakers) if data else [])
        ],
        "speakers": speakers,
        "recent_calls": async_redact_data(
            [r.summary() for r in data.history.records()] if data else [], TO_REDACT
        ),
        "communication_metrics": data.metrics.summary() if data else {},
        "homekit": homekit,
        "audio_verification": (
            manager.audio_verification(entry) if (manager := hass.data.get(SETUP_KEY)) else None
        ),
    }
