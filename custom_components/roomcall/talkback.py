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
from .communication import permitted, validate_targets
from .const import ALL_ROOMS
from .core import CallRecord, speaker_area
from .errors import Problem
from .localization import text

if TYPE_CHECKING:
    from . import RoomCallData

_LOGGER = logging.getLogger(__name__)
LISTEN_SECONDS = 30  # upper bound for the satellite to prompt, listen and transcribe


def satellites(hass: HomeAssistant, areas: set[str]) -> list[str]:
    """One available satellite that can ask a question per area, so a reply plays once."""
    chosen: dict[str, str] = {}
    for state in sorted(hass.states.async_all("assist_satellite"), key=lambda s: s.entity_id):
        area = speaker_area(hass, state.entity_id)
        if (
            area in areas
            and area not in chosen
            and state.state not in (STATE_UNAVAILABLE, STATE_UNKNOWN)
            and state.attributes.get("supported_features", 0) & 2
        ):
            chosen[area] = state.entity_id
    return list(chosen.values())


async def listen(hass: HomeAssistant, data: RoomCallData, record: CallRecord) -> None:
    """After a delivered room-to-room talk call, collect and deliver spoken replies."""
    names = {area.id: area.name for area in ar.async_get(hass).async_list_areas()}
    origin = record.from_room
    targets = {target.strip() for target in record.target.split(",")}
    if (
        not data.talk_back
        or record.kind != "talk"
        or not record.ok
        or origin not in names
        or ALL_ROOMS in targets
        or not hass.services.has_service("assist_satellite", "ask_question")
    ):
        return
    # The caller's permissions apply to the listening device and to the reply's room.
    context = Context(user_id=record.owner)
    ears = [e for e in satellites(hass, targets - {origin}) if await permitted(hass, e, context)]
    if not ears:
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
                )
        except Exception:  # an unanswered or failed question simply means no reply
            _LOGGER.debug("RoomCall talk-back from %s ended without a reply", entity_id)
            return None
        sentence = str((response or {}).get("sentence") or "").strip()
        return sentence or None

    replies = await asyncio.gather(*(ask(entity_id) for entity_id in ears))
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
