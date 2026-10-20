"""Room routing, clip storage, transcoding and fan-out for RoomCall."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.components.media_player import (
    ATTR_MEDIA_ANNOUNCE,
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_EXTRA,
    SERVICE_PLAY_MEDIA,
    MediaPlayerEntityFeature,
    MediaType,
)
from homeassistant.components.media_player import (
    DOMAIN as MEDIA_PLAYER_DOMAIN,
)
from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .const import ALL_ROOMS, CLIP_TTL_SECONDS, SIGNAL_CALL_FINISHED

_LOGGER = logging.getLogger(__name__)

PLAYABLE = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav"}


class TranscodeError(Exception):
    """Recording could not be converted."""


@dataclass(slots=True)
class Clip:
    data: bytes
    content_type: str
    created: float


@dataclass(slots=True)
class Delivery:
    entity_id: str
    name: str
    ok: bool
    elapsed_ms: int
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "speaker": self.name,
            "ok": self.ok,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
        }


@dataclass(slots=True)
class CallRecord:
    target: str
    kind: str
    results: list[Delivery]
    at: Any = field(default_factory=dt_util.utcnow)

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)


class ClipStore:
    """Short-lived in-memory clips that speakers fetch through the clip view."""

    def __init__(self, ttl: float = CLIP_TTL_SECONDS, max_clips: int = 50) -> None:
        self._ttl = ttl
        self._max = max_clips
        self._clips: dict[str, Clip] = {}

    def add(self, data: bytes, content_type: str) -> str:
        self.prune()
        clip_id = secrets.token_urlsafe(16)
        self._clips[clip_id] = Clip(data, content_type, time.monotonic())
        while len(self._clips) > self._max:
            self._clips.pop(next(iter(self._clips)))
        return clip_id

    def get(self, clip_id: str) -> Clip | None:
        self.prune()
        return self._clips.get(clip_id)

    def prune(self) -> None:
        cutoff = time.monotonic() - self._ttl
        for clip_id in [k for k, c in self._clips.items() if c.created < cutoff]:
            del self._clips[clip_id]


def speaker_area(hass: HomeAssistant, entity_id: str) -> str | None:
    """Area of a speaker entity, falling back to its device's area."""
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    if entry.device_id and (device := dr.async_get(hass).async_get(entry.device_id)):
        return device.area_id
    return None


def rooms(hass: HomeAssistant, speakers: list[str]) -> list[dict[str, Any]]:
    """Areas that contain at least one configured speaker, with their speakers."""
    areas = ar.async_get(hass)
    grouped: dict[str, list[str]] = {}
    for entity_id in speakers:
        if (area_id := speaker_area(hass, entity_id)) is not None:
            grouped.setdefault(area_id, []).append(entity_id)
    out = []
    for area_id, members in grouped.items():
        area = areas.async_get_area(area_id)
        out.append(
            {
                "area_id": area_id,
                "name": area.name if area else area_id,
                "icon": area.icon if area else None,
                "speakers": [_friendly(hass, e) for e in members],
            }
        )
    return sorted(out, key=lambda r: r["name"].lower())


def resolve(hass: HomeAssistant, speakers: list[str], targets: list[str]) -> list[str]:
    """Speaker entity ids for area ids, area names, or ``all``."""
    if not targets or ALL_ROOMS in targets:
        return list(speakers)
    areas = ar.async_get(hass)
    wanted: set[str] = set()
    for target in targets:
        if areas.async_get_area(target):
            wanted.add(target)
        elif area := areas.async_get_area_by_name(target):
            wanted.add(area.id)
        elif target in speakers:
            wanted.add(f"entity:{target}")
    return [e for e in speakers if speaker_area(hass, e) in wanted or f"entity:{e}" in wanted]


def _friendly(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    return state.name if state else entity_id


# Walkie-talkie style two-tone "chirp" (C6 then E6) prepended to push-to-talk clips.
_CHIME_FILTER = (
    "[1:a][2:a]concat=n=2:v=0:a=1,volume=0.35,apad=pad_dur=0.12[chime];"
    "[0:a]aresample=44100,aformat=channel_layouts=mono,volume={gain}dB[voice];"
    "[chime][voice]concat=n=2:v=0:a=1[out]"
)


async def to_playable(
    ffmpeg_binary: str, data: bytes, content_type: str, gain_db: float, chime: bool = False
) -> tuple[bytes, str]:
    """Return ``(audio, content_type)`` playable by every speaker (MP3/WAV)."""
    base = content_type.split(";", 1)[0].strip().lower()
    if base in PLAYABLE and not gain_db and not chime:
        return data, "audio/mpeg" if PLAYABLE[base] == "mp3" else "audio/wav"
    if chime:
        graph = [
            "-f",
            "lavfi",
            "-t",
            "0.12",
            "-i",
            "sine=frequency=1047:sample_rate=44100",
            "-f",
            "lavfi",
            "-t",
            "0.2",
            "-i",
            "sine=frequency=1319:sample_rate=44100",
            "-filter_complex",
            _CHIME_FILTER.format(gain=gain_db),
            "-map",
            "[out]",
        ]
    else:
        graph = ["-af", f"volume={gain_db}dB"]
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        *graph,
        "-ac",
        "1",
        "-ar",
        "44100",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "128k",
        "-f",
        "mp3",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(data)
    if proc.returncode != 0 or not out:
        raise TranscodeError(err.decode(errors="replace").strip() or "ffmpeg produced no audio")
    return out, "audio/mpeg"


def mp3_seconds(audio: bytes, bitrate_kbps: int = 128) -> float:
    """Approximate duration of a constant-bitrate MP3."""
    return round(len(audio) * 8 / (bitrate_kbps * 1000), 1)


async def _play_one(
    hass: HomeAssistant,
    entity_id: str,
    media_id: str,
    volume: float | None,
    chime_message: dict[str, Any] | None = None,
) -> Delivery:
    started = time.perf_counter()
    state = hass.states.get(entity_id)
    features = state.attributes.get("supported_features", 0) if state else 0
    announce = bool(features & MediaPlayerEntityFeature.MEDIA_ANNOUNCE)
    if chime_message is not None:
        domain, service = "chime_tts", "say"
        data: dict[str, Any] = {ATTR_ENTITY_ID: entity_id, "announce": announce, **chime_message}
        if volume is not None:
            data["volume_level"] = volume
    else:
        domain, service = MEDIA_PLAYER_DOMAIN, SERVICE_PLAY_MEDIA
        data = {
            ATTR_ENTITY_ID: entity_id,
            ATTR_MEDIA_CONTENT_ID: media_id,
            ATTR_MEDIA_CONTENT_TYPE: MediaType.MUSIC,
        }
        if announce:
            data[ATTR_MEDIA_ANNOUNCE] = True
        if volume is not None:
            data[ATTR_MEDIA_EXTRA] = {"volume": round(volume * 100)}
    try:
        async with asyncio.timeout(30):
            await hass.services.async_call(domain, service, data, blocking=True)
    except Exception as err:
        _LOGGER.warning("RoomCall could not play on %s: %s", entity_id, err)
        return Delivery(
            entity_id,
            _friendly(hass, entity_id),
            False,
            round((time.perf_counter() - started) * 1000),
            str(err) or type(err).__name__,
        )
    return Delivery(
        entity_id, _friendly(hass, entity_id), True, round((time.perf_counter() - started) * 1000)
    )


async def fan_out(
    hass: HomeAssistant,
    entity_ids: list[str],
    media_id: str,
    volume: float | None,
    *,
    target: str,
    kind: str,
    chime_message: dict[str, Any] | None = None,
) -> CallRecord:
    results = await asyncio.gather(
        *(_play_one(hass, e, media_id, volume, chime_message) for e in entity_ids)
    )
    record = CallRecord(target=target, kind=kind, results=list(results))
    async_dispatcher_send(hass, SIGNAL_CALL_FINISHED, record)
    return record
