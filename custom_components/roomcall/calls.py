"""The call pipeline shared by push-to-talk and the ``roomcall.broadcast`` action."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.util import dt as dt_util

from .capabilities import deduplicate
from .const import ALL_ROOMS, API_CLIP, PRIORITY_NORMAL
from .core import (
    CallRecord,
    TranscodeError,
    call_finished,
    fan_out,
    friendly,
    group_names,
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

    def __init__(self, message: str, status: int = 400, code: str = "invalid_request") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


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
    audio: bytes | None = None  # MP3 kept for replay
    recording: bytes | None = None  # original upload, for speech-to-text
    caller_user_id: str | None = None
    seconds: float | None = None  # clip length, when known
    live: LiveStream | None = None  # audio still being recorded
    on_delivered: Callable[[CallRecord], None] | None = None
    on_dispatch: Callable[[], None] | None = None
    deadline: float | None = None
    call_id: str | None = None
    language: str | None = None
    audio_processing: str = "off"
    require_ack: bool = False
    notify: bool = True  # False when the caller sends its own phone notification
    assistant: str | None = None


@dataclass(slots=True)
class Prepared:
    targets: list[str]
    routing: Plan


def _targets(hass: HomeAssistant, data: RoomCallData, request: CallRequest) -> list[str]:
    targets = [t for t in request.targets if t]
    for person in request.people or []:
        preference = data.preferences.get(person, {})
        area = person_area(
            hass,
            person,
            max_age=data.presence_max_age,
            min_confidence=data.presence_min_confidence,
        ) or preference.get("room")
        if area is None:
            name = friendly(hass, person)
            raise CallError(
                f"Can't tell which room {name} is in right now", 404, "presence_unknown"
            )
        targets.append(area)
    if not targets:
        targets = [ALL_ROOMS]
    return targets


def prepare_call(hass: HomeAssistant, data: RoomCallData, request: CallRequest) -> Prepared:
    """Resolve rooms and apply Do Not Disturb, quiet hours and priority (raises CallError)."""
    targets = _targets(hass, data, request)
    entity_ids = resolve(hass, data.speakers, targets)
    entity_ids, duplicates = deduplicate(hass, entity_ids, data.aliases)
    if not entity_ids:
        raise CallError(f"No RoomCall speakers in {', '.join(targets)}", 404, "no_targets")
    maximum = data.agent_policies.playback(
        hass, request.caller_user_id, entity_ids, request.priority, request.volume
    )
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
        raise CallError(
            str(err), 423, "quiet_hours_blocked" if quiet_active(data) else "dnd_blocked"
        ) from err
    routing.skipped.update(duplicates)
    routing.volumes = {
        e: _limited_volume(hass, e, v, min(data.max_volume, maximum))
        for e, v in routing.volumes.items()
    }
    return Prepared(targets, routing)


def _limited_volume(
    hass: HomeAssistant, entity_id: str, value: float | None, maximum: float
) -> float | None:
    if value is None and maximum < 1:
        state = hass.states.get(entity_id)
        try:
            value = float(state.attributes.get("volume_level", maximum)) if state else maximum
        except (TypeError, ValueError):
            value = maximum
    return min(value, maximum) if value is not None else None


def quiet_active(data: RoomCallData) -> bool:
    return not data.dnd and data.quiet.active(dt_util.now())


async def place_call(
    hass: HomeAssistant,
    data: RoomCallData,
    request: CallRequest,
    prepared: Prepared | None = None,
) -> CallRecord:
    prepared = prepared or prepare_call(hass, data, request)
    targets, routing = prepared.targets, prepared.routing
    from .communication import permitted
    from .playback import controlled_entities

    controls = {
        other for entity in routing.entity_ids for other in controlled_entities(hass, entity)
    }
    for entity_id in controls:
        if not await permitted(
            hass, entity_id, Context(user_id=request.caller_user_id), request.assistant
        ):
            raise CallError("forbidden", 403, "forbidden")
    data.agent_policies.authorize(request.caller_user_id, "send")
    if request.kind == "talk":
        data.agent_policies.consume(request.caller_user_id)
    record = CallRecord(
        target=", ".join(targets),
        kind=request.kind,
        results=[],
        from_room=request.from_room,
        priority=request.priority,
        skipped=routing.skipped,
        message=request.message,
        owner=request.caller_user_id,
        requires_ack=request.require_ack,
    )
    if request.call_id:
        record.call_id = request.call_id
    data.metrics.counters["calls"] += 1
    data.metrics.counters["duplicate_routes_suppressed"] += sum(
        reason.startswith("duplicate:") for reason in routing.skipped.values()
    )
    started = time.monotonic()
    media_ids = {
        e: data.evidence.url(hass, request.media_id, record, e) for e in routing.entity_ids
    }
    for entity_id, media_id in media_ids.items():
        data.evidence.watch(hass, record, entity_id, media_id)
    delivery = fan_out(
        hass,
        routing.entity_ids,
        request.media_id,
        None,
        target=record.target,
        kind=request.kind,
        stagger=data.stagger_ms / 1000,
        retry=data.retry,
        restore=data.restore,
        seconds=request.seconds,
        until=request.live.wait_done if request.live is not None else None,
        volumes=routing.volumes,
        record=record,
        media_ids=media_ids,
        deadline=request.deadline,
        on_dispatch=request.on_dispatch,
        assistant=request.assistant,
        audio_seconds=(lambda live=request.live: len(live.pcm) / 32000)
        if request.live is not None
        else None,
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
    for result in record.results:
        record.evidence.setdefault(result.entity_id, {})["request_accepted"] = result.ok
        data.metrics.observe(f"request:{_route(hass, result.entity_id)}", result.elapsed_ms)
        data.metrics.counters["requests_accepted" if result.ok else "requests_failed"] += 1
        data.metrics.counters["retries"] += max(0, result.attempts - 1)
    data.metrics.observe("call_request", (time.monotonic() - started) * 1000)
    data.history.add(record, audio)
    if not record.ok and audio is not None and data.voicemail_enabled and data.mailbox:
        await data.mailbox.add(
            request.caller_user_id,
            record.transcript or record.message or "",
            record.target,
            audio=audio,
            request={"areas": request.targets, "people": request.people or []},
        )
    call_finished(hass, record)
    if data.notify and request.notify:
        names = {a.id: a.name for a in ar.async_get(hass).async_list_areas()}
        names[ALL_ROOMS] = "Everywhere"
        names.update(group_names(hass))
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


def _route(hass: HomeAssistant, entity_id: str) -> str:
    from homeassistant.helpers import entity_registry as er

    entry = er.async_get(hass).async_get(entity_id)
    return entry.platform if entry else "media_player"


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
            data.audio_processing,
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
