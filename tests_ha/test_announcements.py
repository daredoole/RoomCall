"""Scheduled and triggered announcements, specific speakers, and the Assist/AI intent."""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import unquote

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

DOMAIN = "roomcall"


async def _add(hass: HomeAssistant, entry: MockConfigEntry, kind: str, data: dict) -> str:
    """Add an announcement through the real subentry flow, like the UI or an AI agent."""
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, kind), context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], data)
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    return next(
        sid
        for sid, sub in hass.config_entries.async_get_entry(entry.entry_id).subentries.items()
        if sub.title == data["name"]
    )


def _spoken(tts_audio) -> list[str]:
    return [
        unquote(c.args[1].split("message=")[1].split("&")[0]) for c in tts_audio.await_args_list
    ]


def _local(hour: int, minute: int, weekday: int) -> datetime:
    """The next local time on a given weekday (0 = Monday), in the future."""
    now = dt_util.now()
    day = (now + timedelta(days=(weekday - now.weekday()) % 7 + 7)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return dt_util.as_utc(day)


async def test_scheduled_announcement_plays_on_its_days(
    hass: HomeAssistant, setup_roomcall, house, tts_audio
):
    hass.states.async_set("tts.piper", "unknown")
    hass.states.async_set("sensor.outdoor_temperature", "54")
    played = async_mock_service(hass, "media_player", "play_media")
    sid = await _add(
        hass,
        setup_roomcall,
        "scheduled",
        {
            "name": "School bus",
            "time": "07:30:00",
            "days": ["mon", "tue", "wed", "thu", "fri"],
            "message": "Bus in 10 minutes. It is {{ states('sensor.outdoor_temperature') }} out.",
            "rooms": [house["kitchen"]],
            "priority": "normal",
        },
    )
    async_fire_time_changed(hass, _local(7, 30, 0))  # Monday
    await hass.async_block_till_done()
    assert _spoken(tts_audio) == ["Bus in 10 minutes. It is 54 out."]
    assert len(played) == 2  # synthesize once for both kitchen speakers
    played.clear()
    async_fire_time_changed(hass, _local(7, 30, 5))  # Saturday
    await hass.async_block_till_done()
    assert not played

    registry = er.async_get(hass)
    switch = registry.async_get_entity_id("switch", DOMAIN, f"{sid}_enabled")
    button = registry.async_get_entity_id("button", DOMAIN, f"{sid}_play")
    assert switch and button
    assert hass.states.get(switch).attributes["time"] == "07:30:00"

    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    async_fire_time_changed(hass, _local(7, 30, 1))  # Tuesday, but switched off
    await hass.async_block_till_done()
    assert not played

    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    assert len(played) == 2  # "Play now" works even when the schedule is off
    assert hass.states.get(switch).attributes["last_played"] is not None


async def test_triggered_announcement_follows_a_light_switch(
    hass: HomeAssistant, setup_roomcall, house, tts_audio
):
    hass.states.async_set("tts.piper", "unknown")
    hass.states.async_set("switch.porch_light", "off", {"friendly_name": "Porch light"})
    played = async_mock_service(hass, "media_player", "play_media")
    await _add(
        hass,
        setup_roomcall,
        "triggered",
        {
            "name": "Porch",
            "entity": "switch.porch_light",
            "to_state": "on",
            "cooldown": 60,
            "message": "{{ name }} is {{ state }}",
            "rooms": [house["basement"]],
            "priority": "normal",
        },
    )
    hass.states.async_set("switch.porch_light", "on", {"friendly_name": "Porch light"})
    await hass.async_block_till_done()
    assert _spoken(tts_audio) == ["Porch light is on"]
    assert len(played) == 1

    # Off is not the chosen state; on again within the cooldown is ignored.
    hass.states.async_set("switch.porch_light", "off", {"friendly_name": "Porch light"})
    hass.states.async_set("switch.porch_light", "on", {"friendly_name": "Porch light"})
    await hass.async_block_till_done()
    assert len(played) == 1


async def test_triggered_announcement_waits_for_the_hold_time(
    hass: HomeAssistant, setup_roomcall, house
):
    hass.states.async_set("tts.piper", "unknown")
    hass.states.async_set("cover.garage_door", "closed", {"friendly_name": "Garage door"})
    played = async_mock_service(hass, "media_player", "play_media")
    await _add(
        hass,
        setup_roomcall,
        "triggered",
        {
            "name": "Garage left open",
            "entity": "cover.garage_door",
            "to_state": "open",
            "for": {"minutes": 10},
            "cooldown": 0,
            "message": "The garage door has been open for 10 minutes",
            "target_speakers": [],
            "priority": "urgent",
        },
    )
    hass.states.async_set("cover.garage_door", "open", {"friendly_name": "Garage door"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=5))
    hass.states.async_set("cover.garage_door", "closed", {"friendly_name": "Garage door"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert not played  # closed before 10 minutes

    hass.states.async_set("cover.garage_door", "open", {"friendly_name": "Garage door"})
    await hass.async_block_till_done()
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=11))
    await hass.async_block_till_done()
    assert len(played) == 3  # everywhere: no rooms, speakers or people
    assert {c.data["extra"]["volume"] for c in played} == {80}  # urgent


async def test_broadcast_to_specific_speakers(hass: HomeAssistant, setup_roomcall, house):
    hass.states.async_set("media_player.office_display", "idle", {"supported_features": 1048576})
    played = async_mock_service(hass, "media_player", "play_media")
    await hass.services.async_call(
        DOMAIN,
        "broadcast",
        {
            "speakers": ["media_player.kitchen_hub", "media_player.office_display"],
            "media_url": "http://x/a.mp3",
        },
        blocking=True,
    )
    assert sorted(c.data["entity_id"] for c in played) == [
        "media_player.kitchen_hub",
        "media_player.office_display",  # not an intercom speaker, but named directly
    ]


async def test_ai_and_voice_assistants_can_announce(hass: HomeAssistant, setup_roomcall, house):
    hass.states.async_set("tts.piper", "unknown")
    played = async_mock_service(hass, "media_player", "play_media")
    response = await intent.async_handle(
        hass,
        "test",
        "RoomCallAnnounce",
        {"message": {"value": "Dinner is ready"}, "area": {"value": "basement"}},
    )
    assert "Playback requests accepted" in response.speech["plain"]["speech"]
    assert "unconfirmed" in response.speech["plain"]["speech"]
    assert [c.data["entity_id"] for c in played] == ["media_player.basement_sonos"]

    handler = next(h for h in intent.async_get(hass) if h.intent_type == "RoomCallAnnounce")
    assert handler.platforms is None  # offered to every LLM conversation agent
    assert "announce" in handler.description.lower()


async def test_announcement_validation(hass: HomeAssistant, setup_roomcall):
    result = await hass.config_entries.subentries.async_init(
        (setup_roomcall.entry_id, "scheduled"), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {"name": "Empty", "time": "08:00:00", "days": [], "message": " ", "priority": "normal"},
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"message": "no_message", "days": "no_days"}


async def test_reconfigure_an_announcement(hass: HomeAssistant, setup_roomcall, house):
    sid = await _add(
        hass,
        setup_roomcall,
        "scheduled",
        {
            "name": "Bedtime",
            "time": "21:00:00",
            "days": ["sun"],
            "message": "Bedtime",
            "priority": "normal",
        },
    )
    result = await setup_roomcall.start_subentry_reconfigure_flow(hass, sid)
    result = await hass.config_entries.subentries.async_configure(
        result["flow_id"],
        {
            "name": "Bedtime",
            "time": "20:30:00",
            "days": ["sun", "sat"],
            "message": "Bed",
            "priority": "normal",
        },
    )
    assert result["reason"] == "reconfigure_successful"
    await hass.async_block_till_done()
    item = hass.data[DOMAIN].announcer.items[sid]
    assert item.data["time"] == "20:30:00"
    assert item.data["message"] == "Bed"


async def test_only_one_announcement_per_minute_window(hass: HomeAssistant, house):
    """Sanity: no announcements means nothing is scheduled and setup still works."""
    entry = MockConfigEntry(domain=DOMAIN, data={"speakers": ["media_player.basement_sonos"]})
    entry.add_to_hass(hass)
    with patch("custom_components.roomcall.announcements.async_track_time_change") as track:
        assert await hass.config_entries.async_setup(entry.entry_id)
    track.assert_not_called()
    assert hass.data[DOMAIN].announcer.items == {}
