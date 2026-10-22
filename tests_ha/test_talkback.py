"""Hands-free talk-back: a mic satellite in the called room answers back to the caller."""

from __future__ import annotations

import asyncio
import io
import wave
from unittest.mock import patch
from urllib.parse import unquote

from custom_components.roomcall import talkback
from custom_components.roomcall.core import CallRecord, Delivery
from custom_components.roomcall.errors import Problem
from homeassistant.core import HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

DOMAIN = "roomcall"
SPEAKERS = ["media_player.kitchen_sonos", "media_player.kitchen_hub", "media_player.basement_sonos"]


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x10\x00" * 4000)
    return buf.getvalue()


async def _setup(hass: HomeAssistant, house, *, talk_back: bool = True) -> MockConfigEntry:
    registry = er.async_get(hass)
    satellite = registry.async_get_or_create(
        "assist_satellite", "test", "basement_voice", suggested_object_id="basement_voice"
    )
    registry.async_update_entity(satellite.entity_id, area_id=house["basement"])
    hass.states.async_set(satellite.entity_id, "idle", {"supported_features": 2})
    hass.states.async_set("tts.piper", "unknown")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"speakers": SPEAKERS, "volume": 0.4},
        options={"speakers": SPEAKERS, "volume": 0.4, "communication": {"talk_back": talk_back}},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _answers(hass: HomeAssistant, sentence: str | None, *, error: bool = False):
    return async_mock_service(
        hass,
        "assist_satellite",
        "ask_question",
        response={"sentence": sentence} if sentence is not None else {},
        supports_response=SupportsResponse.ONLY,
        raise_exception=HomeAssistantError("timeout") if error else None,
    )


def _record(
    target: str,
    *,
    origin: str | None = "kitchen",
    kind: str = "talk",
    ok: bool = True,
    owner: str | None = None,
    speaker: str = "media_player.basement_sonos",
):
    record = CallRecord(
        target=target,
        kind=kind,
        results=[Delivery(speaker, speaker, ok, 10)],
    )
    record.from_room, record.owner = origin, owner
    return record


async def test_reply_from_the_called_room_plays_in_the_callers_room(
    hass: HomeAssistant, house, hass_client, tts_audio
):
    await _setup(hass, house)
    asked = _answers(hass, "Coming up in a minute")
    played = async_mock_service(hass, "media_player", "play_media")
    client = await hass_client()
    resp = await client.post(
        f"/api/roomcall/talk?target={house['basement']}&from={house['kitchen']}",
        data=_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    assert resp.status == 200
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [c.data["entity_id"] for c in asked] == ["assist_satellite.basement_voice"]
    assert asked[0].data["preannounce"] is False
    spoken = [
        unquote(c.args[1].split("message=")[1].split("&")[0]) for c in tts_audio.await_args_list
    ]
    assert spoken == ["Basement says: Coming up in a minute"]
    # The reply plays in the kitchen, and as a message it never asks for a reply itself.
    kitchen = {"media_player.kitchen_sonos", "media_player.kitchen_hub"}
    assert {c.data["entity_id"] for c in played[1:]} == kitchen
    assert len(asked) == 1
    assert hass.data[DOMAIN].metrics.counters["talk_back_replies"] == 1


async def test_no_reply_is_sent_when_nobody_answers(hass: HomeAssistant, house, tts_audio):
    await _setup(hass, house)
    data = hass.data[DOMAIN]
    for sentence, error in (("  ", False), (None, False), ("x", True)):
        answers = _answers(hass, sentence, error=error)
        tts_audio.reset_mock()
        await talkback.listen(hass, data, _record(house["basement"]))
        assert len(answers) == 1 and not tts_audio.await_args_list


async def test_talk_back_only_listens_when_it_makes_sense(hass: HomeAssistant, house):
    entry = await _setup(hass, house)
    data = hass.data[DOMAIN]
    asked = _answers(hass, "hi")
    basement, kitchen = house["basement"], house["kitchen"]
    for record in (
        _record(basement, kind="message"),  # typed messages and replies
        _record(basement, ok=False),  # nothing played
        _record(basement, origin=None),  # no room to answer to
        _record(basement, origin="Apple Home"),
        _record(f"{basement},all"),  # everywhere
        _record(kitchen, speaker="media_player.kitchen_sonos"),  # no satellite in the kitchen
        # Quiet hours skipped the basement: only the kitchen played, so nobody listens.
        _record(f"{basement},{kitchen}", speaker="media_player.kitchen_sonos"),
        _record(basement, origin=basement),  # calling your own room
    ):
        await talkback.listen(hass, data, record)
    hass.states.async_set(
        "assist_satellite.basement_voice", "unavailable", {"supported_features": 2}
    )
    await talkback.listen(hass, data, _record(basement))
    assert not asked

    # Turned off, or no satellite integration: no question is asked.
    hass.states.async_set("assist_satellite.basement_voice", "idle", {"supported_features": 2})
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, "communication": {"talk_back": False}}
    )
    await hass.async_block_till_done()
    await talkback.listen(hass, hass.data[DOMAIN], _record(basement))
    hass.services.async_remove("assist_satellite", "ask_question")
    hass.data[DOMAIN].talk_back = True
    await talkback.listen(hass, hass.data[DOMAIN], _record(basement))
    assert not asked


async def test_one_satellite_per_room_and_the_callers_permissions_apply(
    hass: HomeAssistant, house, tts_audio
):
    await _setup(hass, house)
    registry = er.async_get(hass)
    second = registry.async_get_or_create(
        "assist_satellite", "test", "basement_echo", suggested_object_id="basement_echo"
    )
    registry.async_update_entity(second.entity_id, area_id=house["basement"])
    hass.states.async_set(second.entity_id, "idle", {"supported_features": 2})
    data = hass.data[DOMAIN]
    asked = _answers(hass, "On my way")
    kitchen, basement = house["kitchen"], house["basement"]

    # Two satellites in the basement: only one asks, and the answer plays once.
    await talkback.listen(hass, data, _record(basement, origin=kitchen))
    await hass.async_block_till_done()
    assert len(asked) == 1 and len(tts_audio.await_args_list) == 1

    # A caller without access to the devices gets no talk-back.
    await talkback.listen(hass, data, _record(basement, origin=kitchen, owner="unknown"))
    assert len(asked) == 1

    # A reply room the caller may not use is skipped.
    tts_audio.reset_mock()
    with patch(
        "custom_components.roomcall.talkback.validate_targets",
        side_effect=Problem("forbidden"),
    ):
        await talkback.listen(hass, data, _record(basement, origin=kitchen))
    assert len(asked) == 2 and not tts_audio.await_args_list


async def test_rooms_by_name_permitted_satellite_and_caller_context(
    hass: HomeAssistant, house, hass_admin_user
):
    await _setup(hass, house)
    registry = er.async_get(hass)
    other = registry.async_get_or_create(
        "assist_satellite", "test", "basement_zz", suggested_object_id="basement_zz"
    )
    registry.async_update_entity(other.entity_id, area_id=house["basement"])
    hass.states.async_set(other.entity_id, "idle", {"supported_features": 2})
    data = hass.data[DOMAIN]
    asked = _answers(hass, None)
    # Rooms given by name match by area; the question runs as the caller.
    await talkback.listen(
        hass, data, _record("Basement", origin="Kitchen", owner=hass_admin_user.id)
    )
    assert asked[0].data["entity_id"] == "assist_satellite.basement_voice"
    assert asked[0].context.user_id == hass_admin_user.id

    # A caller allowed only the later satellite still gets talk-back through it.
    async def only_other(_hass, entity_id, _context, *args, **kwargs):
        return entity_id == other.entity_id

    with patch("custom_components.roomcall.talkback.permitted", only_other):
        await talkback.listen(hass, data, _record(house["basement"]))
    assert asked[1].data["entity_id"] == other.entity_id


async def test_unloading_cancels_a_pending_reply(hass: HomeAssistant, house, tts_audio):
    from homeassistant.helpers.dispatcher import async_dispatcher_send

    entry = await _setup(hass, house)
    waiting = asyncio.Event()

    async def never_answers(call):
        waiting.set()
        await asyncio.Event().wait()

    hass.services.async_register(
        "assist_satellite", "ask_question", never_answers, supports_response=SupportsResponse.ONLY
    )
    async_dispatcher_send(hass, "roomcall_call_finished", _record(house["basement"]))
    await waiting.wait()
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert not tts_audio.await_args_list


async def test_talk_back_waits_for_the_call_to_finish_playing(hass: HomeAssistant, house):
    await _setup(hass, house)
    data = hass.data[DOMAIN]
    asked = _answers(hass, None)
    # The basement speaker is still playing the call: its lease is held.
    lease = await data.scheduler.acquire("media_player.basement_sonos", None)
    listening = hass.async_create_task(talkback.listen(hass, data, _record(house["basement"])))
    await asyncio.sleep(0.05)
    assert not asked
    lease.release()
    await listening
    assert len(asked) == 1

    # A call that never finishes playing gives up instead of talking over it.
    with patch.object(data.scheduler, "acquire", side_effect=TimeoutError):
        await talkback.listen(hass, data, _record(house["basement"]))
    assert len(asked) == 1
