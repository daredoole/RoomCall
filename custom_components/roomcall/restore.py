"""Put a speaker back the way it was after a call.

Speakers without announce support (AV receivers through HEOS, Android TV / Google TV boxes
through Cast) stop whatever they were doing to play a call. RoomCall snapshots the speaker and
the other media players that control the same physical device (for example the Denon's
``denonavr`` entity, which owns the input source, or an onn box's Android TV Remote entity,
which knows the foreground app), then restores input, volume, power and app afterwards.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import STATE_OFF, STATE_PLAYING, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .core import speaker_area

_LOGGER = logging.getLogger(__name__)

# Mirrors of other players: restoring them would fight the real entity.
_IGNORED_PLATFORMS = {"music_assistant", "dlna_dmr"}
POLL_SECONDS = 0.5
UNKNOWN_LENGTH_SECONDS = 45.0
_ACTIVE = "roomcall_restore_active"


@dataclass(slots=True)
class Snapshot:
    entity_id: str
    platform: str | None
    state: str
    source: str | None
    volume: float | None
    app_id: str | None


def _identity(hass: HomeAssistant, entity_id: str) -> tuple[str, str] | None:
    """Normalised (manufacturer, model) of an entity's device."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None or entry.device_id is None:
        return None
    device = dr.async_get(hass).async_get(entry.device_id)
    if device is None or not device.manufacturer or not device.model:
        return None
    maker = device.manufacturer.strip().lower()
    model = re.sub(rf"^{re.escape(maker)}\s+", "", device.model.strip().lower())
    return maker, model


def related_players(hass: HomeAssistant, entity_id: str) -> list[str]:
    """The speaker plus other media players for the same device model in the same area."""
    identity = _identity(hass, entity_id)
    area = speaker_area(hass, entity_id)
    out = [entity_id]
    if identity is None or area is None:
        return out
    registry = er.async_get(hass)
    for other in hass.states.async_entity_ids("media_player"):
        if other == entity_id:
            continue
        entry = registry.async_get(other)
        if entry is None or entry.platform in _IGNORED_PLATFORMS:
            continue
        state = hass.states.get(other)
        if state is None or state.state == STATE_UNAVAILABLE:
            continue
        if speaker_area(hass, other) == area and _identity(hass, other) == identity:
            out.append(other)
    return out


def take(hass: HomeAssistant, entity_ids: list[str]) -> list[Snapshot]:
    registry = er.async_get(hass)
    snaps = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None:
            continue
        entry = registry.async_get(entity_id)
        snaps.append(
            Snapshot(
                entity_id=entity_id,
                platform=entry.platform if entry else None,
                state=state.state,
                source=state.attributes.get("source"),
                volume=state.attributes.get("volume_level"),
                app_id=state.attributes.get("app_id"),
            )
        )
    return snaps


async def wait_until_done(hass: HomeAssistant, entity_id: str, seconds: float | None) -> None:
    """Wait for the call to finish: its length when known, else until playback stops."""
    known = seconds is not None
    minimum = (seconds or 0) + 0.8 if known else 2.0
    deadline = minimum + (8.0 if known else UNKNOWN_LENGTH_SECONDS)
    waited = 0.0
    while waited < deadline:
        await asyncio.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        if waited < minimum:
            continue
        state = hass.states.get(entity_id)
        if state is None or state.state != STATE_PLAYING:
            return


async def restore(hass: HomeAssistant, snaps: list[Snapshot]) -> None:
    for snap in snaps:
        try:
            await _restore_one(hass, snap)
        except Exception as err:
            _LOGGER.warning("RoomCall could not restore %s: %s", snap.entity_id, err)


async def _call(hass: HomeAssistant, service: str, entity_id: str, **data: Any) -> None:
    await hass.services.async_call(
        "media_player", service, {"entity_id": entity_id, **data}, blocking=True
    )


async def _restore_one(hass: HomeAssistant, snap: Snapshot) -> None:
    current = hass.states.get(snap.entity_id)
    if current is None or current.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return
    features = current.attributes.get("supported_features", 0)
    if snap.state == STATE_OFF:
        if current.state != STATE_OFF and features & MediaPlayerEntityFeature.TURN_OFF:
            await _call(hass, "turn_off", snap.entity_id)
        return
    sources = current.attributes.get("source_list") or []
    if (
        snap.source
        and snap.source != current.attributes.get("source")
        and snap.source in sources
        and features & MediaPlayerEntityFeature.SELECT_SOURCE
    ):
        await _call(hass, "select_source", snap.entity_id, source=snap.source)
    if (
        snap.volume is not None
        and current.attributes.get("volume_level") != snap.volume
        and features & MediaPlayerEntityFeature.VOLUME_SET
    ):
        await _call(hass, "volume_set", snap.entity_id, volume_level=snap.volume)
    if (
        snap.app_id
        and snap.platform == "androidtv_remote"
        and current.attributes.get("app_id") != snap.app_id
    ):
        # Reopen the app that was in front (playback position is up to the app).
        await _call(
            hass,
            "play_media",
            snap.entity_id,
            media_content_type="app",
            media_content_id=snap.app_id,
        )


class Guard:
    """Snapshot before a call and restore after it, sharing one snapshot when calls overlap."""

    def __init__(self, hass: HomeAssistant, entity_id: str) -> None:
        self._hass = hass
        self._entity_id = entity_id
        self._active: dict[str, list[Any]] = hass.data.setdefault(_ACTIVE, {})

    def start(self) -> None:
        if entry := self._active.get(self._entity_id):
            entry[1] += 1
        else:
            snaps = take(self._hass, related_players(self._hass, self._entity_id))
            self._active[self._entity_id] = [snaps, 1]

    async def finish(self, seconds: float | None) -> None:
        entry = self._active[self._entity_id]
        try:
            await wait_until_done(self._hass, self._entity_id, seconds)
        finally:
            entry[1] -= 1
            if entry[1] == 0:
                del self._active[self._entity_id]
                await restore(self._hass, entry[0])
