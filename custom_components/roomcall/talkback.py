"""Hands-free talk-back from rooms whose speakers have no usable microphone.

Sonos, HomePod and Nest microphones are closed to other apps. A small Assist satellite in
the same area (Atom Echo, ESP32-S3, Home Assistant Voice) can listen instead: after someone
talks to a room from another room, the satellite asks for a reply, and what it hears is
spoken back in the caller's room. Home Assistant returns the transcription, not the audio,
so the reply plays in the configured voice. Silent replies send nothing.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant
from homeassistant.helpers import area_registry as ar

from .calls import CallError
from .capabilities import physical_key
from .communication import permitted, validate_targets
from .const import ALL_ROOMS
from .core import CallRecord, area_id_for, speaker_area
from .errors import Problem
from .localization import text

if TYPE_CHECKING:
    from . import RoomCallData

_LOGGER = logging.getLogger(__name__)
LISTEN_SECONDS = 30  # upper bound for the satellite to prompt, listen and transcribe
PLAYBACK_WAIT = 300  # longest wait for the original call to finish playing


async def satellites(hass: HomeAssistant, areas: set[str], context: Context) -> list[str]:
    """One usable satellite per area, so each called room is asked once and replies once.

    Only satellites the caller may control are candidates, so a permitted one is never
    passed over for an earlier one they cannot use.
    """
    chosen: dict[str, str] = {}
    for state in sorted(hass.states.async_all("assist_satellite"), key=lambda s: s.entity_id):
        area = speaker_area(hass, state.entity_id)
        if (
            area in areas
            and area not in chosen
            and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            and state.attributes.get("supported_features", 0) & 2
            and await permitted(hass, state.entity_id, context)
        ):
            chosen[area] = state.entity_id
    return list(chosen.values())


async def listen(hass: HomeAssistant, data: RoomCallData, record: CallRecord) -> None:
    """After a delivered room-to-room talk call, collect and deliver spoken replies."""
    names = {area.id: area.name for area in ar.async_get(hass).async_list_areas()}
    # Calls may name rooms by ID, name or alias; satellites are matched by area ID.
    origin = area_id_for(hass, record.from_room or "")
    targets = {area_id_for(hass, t) or t.strip() for t in record.target.split(",")}
    if (
        not data.talk_back
        or record.kind != "talk"
        or not record.ok
        or origin not in names
        or ALL_ROOMS in targets
        or not hass.services.has_service("assist_satellite", "ask_question")
    ):
        return
    # Only rooms whose speakers actually played: quiet hours may have skipped some.
    delivered = [result.entity_id for result in record.results if result.ok]
    heard = {area for entity_id in delivered if (area := speaker_area(hass, entity_id))}
    # The caller's permissions apply to the listening device and to the reply's room.
    context = Context(user_id=record.owner)
    ears = await satellites(hass, heard - {origin}, context)
    if not ears:
        return
    # Ask only once the call has finished playing, so "Reply?" never talks over it, and keep
    # those speakers reserved while listening, so a queued call cannot play over the prompt
    # or be heard as the reply. The scheduler holds each speaker until playback has ended.
    deadline = asyncio.get_running_loop().time() + PLAYBACK_WAIT
    held: list[asyncio.Lock] = []
    keys = {physical_key(hass, e, data.aliases): e for e in delivered}  # one lease per device
    try:
        for entity_id in keys.values():
            held.append(await data.scheduler.acquire(entity_id, deadline))
    except (TimeoutError, RuntimeError):
        for lock in held:
            lock.release()
        return

    async def ask(entity_id: str) -> str | None:
        try:
            async with asyncio.timeout(LISTEN_SECONDS):
                response = await hass.services.async_call(
                    "assist_satellite",
                    "ask_question",
                    {
                        "entity_id": entity_id,
                        "question": text(hass, "talk_back_prompt"),
                        "preannounce": False,
                    },
                    blocking=True,
                    return_response=True,
                    context=context,
                )
        except Exception:  # an unanswered or failed question simply means no reply
            _LOGGER.debug("RoomCall talk-back from %s ended without a reply", entity_id)
            return None
        sentence = str((response or {}).get("sentence") or "").strip()
        return sentence or None

    try:
        replies = await asyncio.gather(*(ask(entity_id) for entity_id in ears))
    finally:
        for lock in held:
            lock.release()
    from .messages import send

    for entity_id, reply in zip(ears, replies, strict=True):
        if reply is None:
            continue
        room = names.get(speaker_area(hass, entity_id) or "", "")
        values = {
            "areas": [origin],
            "message": text(hass, "talk_back_reply", room=room, text=reply),
        }
        try:
            await validate_targets(hass, values, context, None)
        except (CallError, Problem):
            continue
        data.metrics.counters["talk_back_replies"] += 1
        # Sent as a message, not a talk call, so a reply never triggers another reply.
        await send(hass, data, values, context)
