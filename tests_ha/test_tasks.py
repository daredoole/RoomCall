"""Announcement tasks: an alert stays open until someone confirms the chore was done."""

from __future__ import annotations

from datetime import timedelta
from urllib.parse import unquote

from custom_components.roomcall.communication import execute
from homeassistant import config_entries
from homeassistant.core import Context, HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_capture_events,
    async_fire_time_changed,
    async_mock_service,
)

DOMAIN = "roomcall"
SPEAKERS = ["media_player.kitchen_sonos", "media_player.kitchen_hub", "media_player.basement_sonos"]
LAUNDRY = {
    "name": "Laundry",
    "entity": "sensor.dryer",
    "to_state": "done",
    "cooldown": 0,
    "message": "The {{ name }} is done.",
    "task": "Took out the dry clothes",
    "remind_every": 10,
    "priority": "normal",
}


async def _setup(hass: HomeAssistant, admin_id: str) -> MockConfigEntry:
    for name, user in (("Alex Phone", admin_id), ("Sam Phone", "someone-else")):
        MockConfigEntry(
            domain="mobile_app", data={"device_name": name, "user_id": user}
        ).add_to_hass(hass)
    hass.states.async_set("person.alex", "Kitchen", {"user_id": admin_id})
    hass.states.async_set("tts.piper", "unknown")
    hass.states.async_set("sensor.dryer", "running", {"friendly_name": "Dryer"})
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"speakers": SPEAKERS, "volume": 0.4},
        options={
            "speakers": SPEAKERS,
            "volume": 0.4,
            "replies": {"notify": ["notify.mobile_app_alex_phone", "notify.mobile_app_sam_phone"]},
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def _add(hass: HomeAssistant, entry: MockConfigEntry, kind: str, data: dict) -> str:
    result = await hass.config_entries.subentries.async_init(
        (entry.entry_id, kind), context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.subentries.async_configure(result["flow_id"], data)
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    await hass.async_block_till_done()
    return next(
        sid
        for sid, sub in hass.config_entries.async_get_entry(entry.entry_id).subentries.items()
        if sub.title == data["name"]
    )


def _spoken(tts_audio) -> list[str]:
    """Each message is synthesized once, then played on every speaker."""
    return [
        unquote(c.args[1].split("message=")[1].split("&")[0]) for c in tts_audio.await_args_list
    ]


async def _advance(hass: HomeAssistant, minutes: float) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(minutes=minutes))
    await hass.async_block_till_done()


async def _action(hass: HomeAssistant, action: str, user_id: str | None) -> None:
    hass.bus.async_fire(
        "mobile_app_notification_action", {"action": action}, context=Context(user_id=user_id)
    )
    await hass.async_block_till_done()


async def test_laundry_stays_open_until_someone_takes_the_clothes_out(
    hass: HomeAssistant, house, hass_admin_user, tts_audio
):
    alex = async_mock_service(hass, "notify", "mobile_app_alex_phone")
    sam = async_mock_service(hass, "notify", "mobile_app_sam_phone")
    entry = await _setup(hass, hass_admin_user.id)
    played = async_mock_service(hass, "media_player", "play_media")
    sid = await _add(
        hass,
        entry,
        "triggered",
        {**LAUNDRY, "people": ["person.alex"]},
    )
    registry = er.async_get(hass)
    sensor = registry.async_get_entity_id("sensor", DOMAIN, f"{sid}_task")
    assert hass.states.get(sensor).state == "idle"
    completed = async_capture_events(hass, "roomcall_task_completed")

    hass.states.async_set("sensor.dryer", "done", {"friendly_name": "Dryer"})
    await hass.async_block_till_done()
    assert _spoken(tts_audio) == ["The Dryer is done."] and len(played) == 2
    assert hass.states.get(sensor).state == "open"
    # Only Alex's phone: the task notification replaces the generic call notification.
    assert not sam and len(alex) == 1
    note = alex[0].data
    assert note["title"] == "Laundry" and note["message"] == "The Dryer is done."
    assert note["data"]["tag"] == f"roomcall_task_{sid}" and note["data"]["persistent"]
    assert [a["title"] for a in note["data"]["actions"]] == [
        "Took out the dry clothes",
        "Remind me later",
    ]
    assert "push" not in note["data"]

    # Swiping the notification away is not confirmation.
    hass.bus.async_fire("mobile_app_notification_cleared", {"tag": f"roomcall_task_{sid}"})
    await _action(hass, "SOMETHING_ELSE", hass_admin_user.id)
    hass.bus.async_fire("mobile_app_notification_action", {"action": 42})
    played.clear()
    alex.clear()
    tts_audio.reset_mock()
    await _advance(hass, 10)
    # The reminder replays what the dryer said when the task opened.
    assert _spoken(tts_audio) == ["The Dryer is done."] and len(played) == 2
    assert len(alex) == 1 and hass.states.get(sensor).state == "open"

    # "Remind me later" clears the notification and waits 30 minutes.
    alex.clear()
    played.clear()
    await _action(hass, f"ROOMCALL_TASK_SNOOZE_{sid}", hass_admin_user.id)
    assert alex[0].data == {
        "message": "clear_notification",
        "data": {"tag": f"roomcall_task_{sid}"},
    }
    await _advance(hass, 10)
    assert not played
    await _advance(hass, 30)
    assert len(played) == 2

    alex.clear()
    played.clear()
    await _action(hass, f"ROOMCALL_TASK_DONE_{sid}", hass_admin_user.id)
    state = hass.states.get(sensor)
    assert state.state == "done"
    assert state.attributes["done_by"] == hass_admin_user.name
    assert state.attributes["task"] == "Took out the dry clothes"
    assert completed[0].data["user_id"] == hass_admin_user.id
    assert completed[0].data["announcement_id"] == sid
    assert completed[0].data["opened"]
    assert alex[0].data["message"] == "clear_notification"
    switch = registry.async_get_entity_id("switch", DOMAIN, f"{sid}_enabled")
    assert hass.states.get(switch).attributes["task_state"] == "done"
    await _advance(hass, 120)
    assert not played

    # A second press of the old notification changes nothing.
    await _action(hass, f"ROOMCALL_TASK_DONE_{sid}", hass_admin_user.id)
    await _action(hass, f"ROOMCALL_TASK_SNOOZE_{sid}", hass_admin_user.id)
    assert len(completed) == 1


async def test_open_tasks_survive_a_reload_and_keep_reminding(
    hass: HomeAssistant, house, hass_admin_user, hass_storage, tts_audio
):
    async_mock_service(hass, "notify", "mobile_app_alex_phone")
    async_mock_service(hass, "notify", "mobile_app_sam_phone")
    entry = await _setup(hass, hass_admin_user.id)
    played = async_mock_service(hass, "media_player", "play_media")
    sid = await _add(hass, entry, "triggered", {**LAUNDRY, "rooms": [house["basement"]]})
    hass.states.async_set("sensor.dryer", "done", {"friendly_name": "Dryer"})
    await hass.async_block_till_done()
    await hass.async_block_till_done()
    played.clear()
    other = await _add(
        hass, entry, "scheduled", {"name": "Bins", "time": "19:00:00", "message": "Bins"}
    )
    # Stale and task-less saved state is ignored.
    stored = hass_storage[f"roomcall.{entry.entry_id}.tasks"]["data"]
    assert set(stored) == {sid}
    hass_storage[f"roomcall.{entry.entry_id}.tasks"]["data"] = {
        **stored,
        "gone": stored[sid],
        other: stored[sid],
    }
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    sensor = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{sid}_task")
    assert hass.states.get(sensor).state == "open"
    assert er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{other}_task") is None
    tts_audio.reset_mock()
    await _advance(hass, 10)
    assert _spoken(tts_audio) == ["The Dryer is done."] and len(played) == 1

    # Paused announcements keep the task open without speaking.
    switch = er.async_get(hass).async_get_entity_id("switch", DOMAIN, f"{sid}_enabled")
    await hass.services.async_call("switch", "turn_off", {"entity_id": switch}, blocking=True)
    played.clear()
    await _advance(hass, 10)
    assert not played and hass.states.get(sensor).state == "open"
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    await _advance(hass, 10)
    assert len(played) == 1
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_tasks_from_the_card_api_buttons_and_agents(
    hass: HomeAssistant, house, hass_admin_user
):
    alex = async_mock_service(hass, "notify", "mobile_app_alex_phone")
    sam = async_mock_service(hass, "notify", "mobile_app_sam_phone")
    entry = await _setup(hass, hass_admin_user.id)
    played = async_mock_service(hass, "media_player", "play_media")
    sid = await _add(
        hass,
        entry,
        "scheduled",
        {
            "name": "Dishwasher",
            "time": "20:00:00",
            "message": "Empty the dishwasher",
            "rooms": [house["kitchen"]],
            "task": "  Emptied it  ",
            "remind_every": 0,
            "priority": "urgent",
        },
    )
    await _add(hass, entry, "scheduled", {"name": "Plain", "time": "21:00:00", "message": "Hi"})
    registry = er.async_get(hass)
    button = registry.async_get_entity_id("button", DOMAIN, f"{sid}_complete")
    sensor = registry.async_get_entity_id("sensor", DOMAIN, f"{sid}_task")
    context = Context(user_id=hass_admin_user.id)

    listed = await execute(hass, "tasks", {}, context)
    assert listed["ok"] and listed["tasks"] == [
        {
            "id": sid,
            "name": "Dishwasher",
            "task": "Emptied it",
            "state": "idle",
            "opened": None,
            "done": None,
            "done_by": None,
            "remind_every": 0,
            "entity_id": sensor,
        }
    ]
    # Nothing is open yet: the button does nothing, the API explains why.
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    assert hass.states.get(sensor).state == "idle"
    refused = await execute(hass, "tasks", {"action": "complete", "id": sid}, context)
    assert refused["error"]["code"] == "task_not_open"
    missing = await execute(hass, "tasks", {"action": "snooze", "id": "nope"}, context)
    assert missing["error"]["code"] == "not_found"

    play = registry.async_get_entity_id("button", DOMAIN, f"{sid}_play")
    await hass.services.async_call("button", "press", {"entity_id": play}, blocking=True)
    await hass.async_block_till_done()
    assert hass.states.get(sensor).state == "open"
    # Without people, every phone hears about it; urgent tasks are time-sensitive.
    assert len(alex) == len(sam) == 1
    assert alex[0].data["data"]["push"] == {"interruption-level": "time-sensitive"}
    played.clear()
    await _advance(hass, 180)
    assert not played  # remind_every 0 plays once

    snoozed = await execute(hass, "tasks", {"action": "snooze", "id": sid, "minutes": 5}, context)
    assert snoozed["tasks"][0]["state"] == "open"
    await _advance(hass, 5)
    assert len(played) == 2

    await hass.services.async_call(
        "button", "press", {"entity_id": button}, blocking=True, context=context
    )
    assert hass.states.get(sensor).state == "done"
    assert hass.states.get(sensor).attributes["done_by"] == hass_admin_user.name

    await hass.services.async_call("button", "press", {"entity_id": play}, blocking=True)
    await hass.async_block_till_done()
    done = await execute(hass, "tasks", {"action": "complete", "id": sid}, Context())
    assert done["tasks"][0]["state"] == "done" and done["tasks"][0]["done_by"] is None


async def test_task_notifies_phones_even_when_no_speaker_can_play(
    hass: HomeAssistant, house, hass_admin_user
):
    alex = async_mock_service(hass, "notify", "mobile_app_alex_phone")
    async_mock_service(hass, "notify", "mobile_app_sam_phone")
    entry = await _setup(hass, hass_admin_user.id)
    async_mock_service(hass, "media_player", "play_media")
    hass.states.async_remove("tts.piper")
    sid = await _add(hass, entry, "triggered", {**LAUNDRY, "people": ["person.nobody"]})
    hass.states.async_set("sensor.dryer", "done", {"friendly_name": "Dryer"})
    await hass.async_block_till_done()
    item = hass.data[DOMAIN].announcer.items[sid]
    assert item.last_error and item.task_state == "open"
    # The listed person has no phone: everyone is told rather than no one.
    assert len(alex) == 1
