"""The call pipeline shared by push-to-talk and the ``roomcall.broadcast`` action."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.util import dt as dt_util

from .const import ALL_ROOMS, PRIORITY_NORMAL
from .core import CallRecord, call_finished, fan_out, friendly, resolve, to_speech_pcm
from .policy import DoNotDisturbError, person_area, plan
from .replies import notify_call, transcribe

if TYPE_CHECKING:
    from . import RoomCallData

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


async def place_call(hass: HomeAssistant, data: RoomCallData, request: CallRequest) -> CallRecord:
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
    if data.stt_engine and request.recording:
        _, record.transcript = await asyncio.gather(delivery, _transcript(hass, data, request))
    else:
        await delivery
    data.history.add(record, request.audio)
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


async def _transcript(hass: HomeAssistant, data: RoomCallData, request: CallRequest) -> str | None:
    assert data.stt_engine and request.recording
    try:
        pcm = await to_speech_pcm(get_ffmpeg_manager(hass).binary, request.recording)
        return await transcribe(hass, data.stt_engine, pcm)
    except Exception:
        _LOGGER.exception("RoomCall speech-to-text failed")
        return None
