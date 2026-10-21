"""The call pipeline shared by push-to-talk and the ``roomcall.broadcast`` action."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .const import ALL_ROOMS, API_CLIP, PRIORITY_NORMAL
from .core import (
    CallRecord,
    TranscodeError,
    call_finished,
    fan_out,
    friendly,
    mp3_seconds,
    resolve,
    to_playable,
    to_speech_pcm,
)
from .policy import DoNotDisturbError, Plan, person_area, plan
from .replies import notify_call, transcribe

if TYPE_CHECKING:
    from . import RoomCallData
    from .live import LiveStream

_LOGGER = logging.getLogger(__name__)


class CallError(Exception):
    """A call could not be placed; the message is shown to the user."""

    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(slots=True)
class CallRequest:
    targets: list[str]
    media_id: str
    kind: str
    people: list[str] | None = None
    priority: str = PRIORITY_NORMAL
    from_room: str | None = None
    volume: float | None = None
    message: str | None = None
    chime_message: dict[str, Any] | None = None
    audio: bytes | None = None  # MP3 kept for replay
    recording: bytes | None = None  # original upload, for speech-to-text
    caller_user_id: str | None = None
    seconds: float | None = None  # clip length, when known
    live: LiveStream | None = None  # audio still being recorded
    on_delivered: Callable[[CallRecord], None] | None = None


@dataclass(slots=True)
class Prepared:
    targets: list[str]
    routing: Plan


def _targets(hass: HomeAssistant, data: RoomCallData, request: CallRequest) -> list[str]:
    targets = [t for t in request.targets if t]
    for person in request.people or []:
        area = person_area(hass, person)
        if area is None:
            name = friendly(hass, person)
            raise CallError(f"Can't tell which room {name} is in right now", 404)
        targets.append(area)
    if not targets:
        targets = [ALL_ROOMS]
    return targets


def prepare_call(hass: HomeAssistant, data: RoomCallData, request: CallRequest) -> Prepared:
    """Resolve rooms and apply Do Not Disturb, quiet hours and priority (raises CallError)."""
    targets = _targets(hass, data, request)
    entity_ids = resolve(hass, data.speakers, targets)
    if not entity_ids:
        raise CallError(f"No RoomCall speakers in {', '.join(targets)}", 404)
    try:
        routing = plan(
            hass,
            entity_ids,
            base_volume=request.volume if request.volume is not None else data.volume,
            priority=request.priority,
            dnd=data.dnd,
            quiet=data.quiet,
            urgent_volume=data.urgent_volume,
            now=dt_util.now(),
        )
    except DoNotDisturbError as err:
        raise CallError(str(err), 423) from err
    return Prepared(targets, routing)


async def place_call(
    hass: HomeAssistant,
    data: RoomCallData,
    request: CallRequest,
    prepared: Prepared | None = None,
) -> CallRecord:
    prepared = prepared or prepare_call(hass, data, request)
    targets, routing = prepared.targets, prepared.routing
    record = CallRecord(
        target=", ".join(targets),
        kind=request.kind,
        results=[],
        from_room=request.from_room,
        priority=request.priority,
        skipped=routing.skipped,
        message=request.message,
    )
    delivery = fan_out(
        hass,
        routing.entity_ids,
        request.media_id,
        None,
        target=record.target,
        kind=request.kind,
        chime_message=request.chime_message,
        stagger=data.stagger_ms / 1000,
        retry=data.retry,
        restore=data.restore,
        seconds=request.seconds,
        volumes=routing.volumes,
        record=record,
    )
    audio = request.audio
    if request.live is not None:
        await delivery
        if request.on_delivered:
            request.on_delivered(record)
        # History, transcript and notifications wait for the end of the live recording.
        await request.live.wait_done()
        audio = bytes(request.live.mp3) or None
        if data.stt_engine and request.live.pcm:
            record.transcript = await _transcribe_pcm(hass, data, bytes(request.live.pcm))
    elif data.stt_engine and request.recording:
        _, record.transcript = await asyncio.gather(delivery, _transcript(hass, data, request))
    else:
        await delivery
    if request.on_delivered and request.live is None:
        request.on_delivered(record)
    data.history.add(record, audio)
    call_finished(hass, record)
    if data.notify:
        names = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
        names[ALL_ROOMS] = "Everywhere"
        try:
            await notify_call(
                hass,
                record,
                services=data.notify,
                dashboard_path=data.reply_path,
                caller_user_id=request.caller_user_id,
                room_names=names,
            )
        except Exception:
            _LOGGER.exception("RoomCall could not send call notifications")
    return record


async def place_recorded_call(
    hass: HomeAssistant,
    data: RoomCallData,
    recording: bytes,
    content_type: str,
    *,
    targets: list[str],
    people: list[str] | None = None,
    priority: str = PRIORITY_NORMAL,
    from_label: str | None = None,
    volume: float | None = None,
    caller_user_id: str | None = None,
) -> tuple[CallRecord, bytes, str]:
    """Convert a voice recording (chime in front) and play it as a call."""
    try:
        audio, playable_type = await to_playable(
            get_ffmpeg_manager(hass).binary,
            recording,
            content_type,
            data.gain_db,
            data.chime_style,
            data.chime_url,
        )
    except TranscodeError as err:
        raise CallError(f"Could not convert recording: {err}", 422) from err
    clip_id = data.clips.add(audio, playable_type)
    ext = "mp3" if playable_type == "audio/mpeg" else "wav"
    try:
        base = get_url(hass, allow_internal=True, allow_external=True, prefer_external=False)
    except NoURLAvailableError as err:
        raise CallError("Set an internal URL under Settings > System > Network", 500) from err
    is_mp3 = playable_type == "audio/mpeg"
    record = await place_call(
        hass,
        data,
        CallRequest(
            targets=targets,
            people=people,
            media_id=f"{base}{API_CLIP.format(clip=f'{clip_id}.{ext}')}",
            kind="talk",
            priority=priority,
            from_room=from_label,
            volume=volume,
            audio=audio if is_mp3 else None,
            recording=recording,
            caller_user_id=caller_user_id,
            seconds=mp3_seconds(audio) if is_mp3 else None,
        ),
    )
    return record, audio, playable_type


async def _transcribe_pcm(hass: HomeAssistant, data: RoomCallData, pcm: bytes) -> str | None:
    assert data.stt_engine
    try:
        return await transcribe(hass, data.stt_engine, pcm)
    except Exception:
        _LOGGER.exception("RoomCall speech-to-text failed")
        return None


async def _transcript(hass: HomeAssistant, data: RoomCallData, request: CallRequest) -> str | None:
    assert data.stt_engine and request.recording
    try:
        pcm = await to_speech_pcm(get_ffmpeg_manager(hass).binary, request.recording)
        return await transcribe(hass, data.stt_engine, pcm)
    except Exception:
        _LOGGER.exception("RoomCall speech-to-text failed")
        return None
