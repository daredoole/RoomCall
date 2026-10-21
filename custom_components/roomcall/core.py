"""Room routing, clip storage, transcoding and fan-out for RoomCall."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
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

from . import chimes
from .const import (
    ALL_ROOMS,
    CLIP_TTL_SECONDS,
    HISTORY_SIZE,
    HISTORY_TTL_SECONDS,
    PRIORITY_NORMAL,
    SIGNAL_CALL_FINISHED,
)

_LOGGER = logging.getLogger(__name__)

PLAYABLE = {"audio/mpeg": "mp3", "audio/mp3": "mp3", "audio/wav": "wav", "audio/x-wav": "wav"}
RETRY_DELAY_SECONDS = 1.0


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
    attempts: int = 1

    def as_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "speaker": self.name,
            "ok": self.ok,
            "elapsed_ms": self.elapsed_ms,
            "error": self.error,
            "attempts": self.attempts,
        }


@dataclass(slots=True)
class CallRecord:
    target: str
    kind: str
    results: list[Delivery]
    at: Any = field(default_factory=dt_util.utcnow)
    call_id: str = field(default_factory=lambda: secrets.token_hex(6))
    from_room: str | None = None
    priority: str = PRIORITY_NORMAL
    skipped: dict[str, str] = field(default_factory=dict)
    transcript: str | None = None
    message: str | None = None
    has_audio: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.call_id,
            "at": self.at.isoformat(),
            "from": self.from_room,
            "target": self.target,
            "kind": self.kind,
            "priority": self.priority,
            "ok": self.ok,
            "delivered": [r.name for r in self.results if r.ok],
            "failed": {r.name: r.error for r in self.results if not r.ok},
            "skipped": self.skipped,
            "text": self.transcript or self.message,
            "has_audio": self.has_audio,
        }


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


class History:
    """Recent calls, newest first, with their audio kept for replay."""

    def __init__(self, size: int = HISTORY_SIZE, ttl: float = HISTORY_TTL_SECONDS) -> None:
        self._size = size
        self._audio = ClipStore(ttl=ttl, max_clips=size)
        self._records: OrderedDict[str, tuple[CallRecord, str | None]] = OrderedDict()

    def add(self, record: CallRecord, audio: bytes | None = None) -> None:
        clip_id = self._audio.add(audio, "audio/mpeg") if audio else None
        record.has_audio = clip_id is not None
        self._records[record.call_id] = (record, clip_id)
        self._records.move_to_end(record.call_id, last=False)
        while len(self._records) > self._size:
            self._records.popitem(last=True)

    def records(self) -> list[CallRecord]:
        return [record for record, _ in self._records.values()]

    def audio(self, call_id: str) -> Clip | None:
        entry = self._records.get(call_id)
        if entry is None or entry[1] is None:
            return None
        return self._audio.get(entry[1])


def speaker_area(hass: HomeAssistant, entity_id: str) -> str | None:
    """Area of an entity, falling back to its device's area."""
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
                "speakers": [friendly(hass, e) for e in members],
            }
        )
    return sorted(out, key=lambda r: r["name"].lower())


def area_id_for(hass: HomeAssistant, value: str) -> str | None:
    """Area id for an area id, name or alias (case-insensitive)."""
    areas = ar.async_get(hass)
    if areas.async_get_area(value):
        return value
    if area := areas.async_get_area_by_name(value):
        return area.id
    lowered = value.strip().lower()
    for area in areas.async_list_areas():
        if area.name.lower() == lowered or lowered in (a.lower() for a in area.aliases):
            return area.id
    return None


def resolve(hass: HomeAssistant, speakers: list[str], targets: list[str]) -> list[str]:
    """Speaker entity ids for area ids, area names, speaker entity ids, or ``all``."""
    if not targets or ALL_ROOMS in targets:
        return list(speakers)
    # A media player named directly plays even if it is not one of the intercom speakers.
    direct = [
        t
        for t in targets
        if t.startswith("media_player.") and t not in speakers and hass.states.get(t) is not None
    ]
    wanted: set[str] = set()
    for target in targets:
        if target in speakers:
            wanted.add(f"entity:{target}")
        elif area_id := area_id_for(hass, target):
            wanted.add(area_id)
    chosen = [e for e in speakers if speaker_area(hass, e) in wanted or f"entity:{e}" in wanted]
    return chosen + direct


def friendly(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    return state.name if state else entity_id


def _encode_args() -> list[str]:
    return ["-ac", "1", "-ar", "44100", "-codec:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"]


async def _run_ffmpeg(ffmpeg_binary: str, args: list[str], data: bytes | None) -> bytes:
    proc = await asyncio.create_subprocess_exec(
        ffmpeg_binary,
        "-hide_banner",
        "-loglevel",
        "error",
        *args,
        stdin=asyncio.subprocess.PIPE if data is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate(data)
    if proc.returncode != 0 or not out:
        raise TranscodeError(err.decode(errors="replace").strip() or "ffmpeg produced no audio")
    return out


async def to_playable(
    ffmpeg_binary: str,
    data: bytes,
    content_type: str,
    gain_db: float,
    chime: str | None = None,
    chime_url: str | None = None,
) -> tuple[bytes, str]:
    """Return ``(audio, content_type)`` playable by every speaker (MP3/WAV).

    ``chime`` is a built-in style from :mod:`chimes`; ``chime_url`` (a file or URL ffmpeg can
    read) takes precedence over it. Either is prepended to the recording.
    """
    base = content_type.split(";", 1)[0].strip().lower()
    if chime_url:
        inputs = ["-i", chime_url]
        graph = chimes.chime_filter(1, 1, from_file=True)
    elif chime in chimes.STYLES:
        inputs = chimes.tone_inputs(chime)
        graph = chimes.chime_filter(1, len(chimes.STYLES[chime]))
    else:
        inputs, graph = [], ""
    if base in PLAYABLE and not gain_db and not graph:
        return data, "audio/mpeg" if PLAYABLE[base] == "mp3" else "audio/wav"
    if graph:
        voice = f"[0:a]aresample=44100,aformat=channel_layouts=mono,volume={gain_db}dB[voice]"
        args = [
            "-i",
            "pipe:0",
            *inputs,
            "-filter_complex",
            f"{graph};{voice};[chime][voice]concat=n=2:v=0:a=1[out]",
            "-map",
            "[out]",
        ]
    else:
        args = ["-i", "pipe:0", "-af", f"volume={gain_db}dB"]
    out = await _run_ffmpeg(ffmpeg_binary, [*args, *_encode_args(), "pipe:1"], data)
    return out, "audio/mpeg"


async def render_chime(ffmpeg_binary: str, style: str) -> bytes:
    """A built-in chime on its own as MP3, for previews and Chime TTS."""
    if style not in chimes.STYLES:
        raise TranscodeError(f"Unknown chime {style!r}")
    args = [
        *chimes.tone_inputs(style),
        "-filter_complex",
        chimes.chime_filter(0, len(chimes.STYLES[style])),
        "-map",
        "[chime]",
    ]
    return await _run_ffmpeg(ffmpeg_binary, [*args, *_encode_args(), "pipe:1"], None)


async def to_speech_pcm(ffmpeg_binary: str, data: bytes) -> bytes:
    """16 kHz mono 16-bit PCM, the format speech-to-text engines expect."""
    args = ["-i", "pipe:0", "-ac", "1", "-ar", "16000", "-f", "s16le", "pipe:1"]
    return await _run_ffmpeg(ffmpeg_binary, args, data)


def mp3_seconds(audio: bytes, bitrate_kbps: int = 128) -> float:
    """Approximate duration of a constant-bitrate MP3."""
    return round(len(audio) * 8 / (bitrate_kbps * 1000), 1)


async def _play_once(
    hass: HomeAssistant,
    entity_id: str,
    media_id: str,
    volume: float | None,
    chime_message: dict[str, Any] | None,
) -> None:
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
    async with asyncio.timeout(30):
        await hass.services.async_call(domain, service, data, blocking=True)


def supports_announce(hass: HomeAssistant, entity_id: str) -> bool:
    state = hass.states.get(entity_id)
    features = state.attributes.get("supported_features", 0) if state else 0
    return bool(features & MediaPlayerEntityFeature.MEDIA_ANNOUNCE)


async def _play_one(
    hass: HomeAssistant,
    entity_id: str,
    media_id: str,
    volume: float | None,
    chime_message: dict[str, Any] | None,
    *,
    delay: float = 0.0,
    retry: bool = False,
    restore: bool = False,
    seconds: float | None = None,
    until: Callable[[], Awaitable[None]] | None = None,
) -> Delivery:
    if delay:
        await asyncio.sleep(delay)
    guard = None
    if restore and not supports_announce(hass, entity_id):
        from .restore import Guard  # imported here to avoid an import cycle

        guard = Guard(hass, entity_id)
        guard.start()
    try:
        return await _attempt(hass, entity_id, media_id, volume, chime_message, retry)
    finally:
        if guard is not None:
            hass.async_create_background_task(
                guard.finish(seconds, until), f"roomcall restore {entity_id}"
            )


async def _attempt(
    hass: HomeAssistant,
    entity_id: str,
    media_id: str,
    volume: float | None,
    chime_message: dict[str, Any] | None,
    retry: bool,
) -> Delivery:
    started = time.perf_counter()
    attempts = 0
    error: str | None = None
    for attempt in range(2 if retry else 1):
        if attempt:
            await asyncio.sleep(RETRY_DELAY_SECONDS)
        attempts += 1
        try:
            await _play_once(hass, entity_id, media_id, volume, chime_message)
        except Exception as err:
            error = str(err) or type(err).__name__
            _LOGGER.warning("RoomCall could not play on %s (try %d): %s", entity_id, attempts, err)
            continue
        error = None
        break
    return Delivery(
        entity_id,
        friendly(hass, entity_id),
        error is None,
        round((time.perf_counter() - started) * 1000),
        error,
        attempts,
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
    stagger: float = 0.0,
    retry: bool = False,
    volumes: dict[str, float | None] | None = None,
    record: CallRecord | None = None,
    restore: bool = False,
    seconds: float | None = None,
    until: Callable[[], Awaitable[None]] | None = None,
) -> CallRecord:
    """Play on every speaker, starting each ``stagger`` seconds after the previous one.

    Several Sonos speakers in one household can drop an announcement that arrives at the
    same moment as another one, so a small offset makes delivery reliable.
    """
    results = await asyncio.gather(
        *(
            _play_one(
                hass,
                e,
                media_id,
                (volumes or {}).get(e, volume),
                chime_message,
                delay=i * stagger,
                retry=retry,
                restore=restore,
                seconds=seconds,
                until=until,
            )
            for i, e in enumerate(entity_ids)
        )
    )
    if record is None:
        record = CallRecord(target=target, kind=kind, results=list(results))
    else:
        record.results = list(results)
    return record


def call_finished(hass: HomeAssistant, record: CallRecord) -> None:
    async_dispatcher_send(hass, SIGNAL_CALL_FINISHED, record)
