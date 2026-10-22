"""Hands-free talk-back: a mic satellite in the called room answers back to the caller."""

from __future__ import annotations

import io
import wave
from urllib.parse import unquote

from custom_components.roomcall import talkback
from custom_components.roomcall.core import CallRecord, Delivery
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


def _record(target: str, *, origin: str | None = "kitchen", kind: str = "talk", ok: bool = True):
    record = CallRecord(
        target=target,
        kind=kind,
        results=[Delivery("media_player.basement_sonos", "Basement", ok, 10)],
    )
    record.from_room = origin
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
        _record(kitchen),  # no satellite in the kitchen
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
