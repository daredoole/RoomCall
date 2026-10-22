"""Answer the door from any room: announcement, phone notification with Answer, card info."""

from __future__ import annotations

from unittest.mock import patch
from urllib.parse import unquote

from custom_components.roomcall.calls import CallError
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

DOMAIN = "roomcall"
SPEAKERS = ["media_player.kitchen_sonos", "media_player.kitchen_hub", "media_player.basement_sonos"]


async def _setup(hass: HomeAssistant, house, **doorbell) -> MockConfigEntry:
    from homeassistant.helpers import area_registry as ar

    front = ar.async_get(hass).async_create("Front Door")
    registry = er.async_get(hass)
    camera = registry.async_get_or_create(
        "camera", "test", "front_door", suggested_object_id="front_door"
    )
    registry.async_update_entity(camera.entity_id, area_id=front.id)
    hass.states.async_set("camera.front_door", "idle", {"entity_picture": "/api/camera_proxy/x"})
    hass.states.async_set("binary_sensor.front_door_visitor", "off", {"friendly_name": "Visitor"})
    hass.states.async_set("tts.piper", "unknown")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"speakers": SPEAKERS, "volume": 0.4},
        options={
            "speakers": SPEAKERS,
            "volume": 0.4,
            "replies": {"notify": ["notify.mobile_app_alex_phone"]},
            "communication": doorbell,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


def _spoken(tts_audio) -> list[str]:
    return [
        unquote(c.args[1].split("message=")[1].split("&")[0]) for c in tts_audio.await_args_list
    ]


async def _press(hass: HomeAssistant, state: str = "on") -> None:
    hass.states.async_set("binary_sensor.front_door_visitor", state)
    await hass.async_block_till_done()


async def test_doorbell_rings_rooms_phones_and_cards(
    hass: HomeAssistant, house, hass_client, tts_audio, monkeypatch
):
    phone = async_mock_service(hass, "notify", "mobile_app_alex_phone")
    clock = [3.0]  # just after boot: the first press still rings
    monkeypatch.setattr("custom_components.roomcall.doorbell.monotonic", lambda: clock[0])
    await _setup(
        hass,
        house,
        doorbell_entity="binary_sensor.front_door_visitor",
        doorbell_camera="camera.front_door",
        doorbell_rooms=[house["kitchen"]],
    )
    played = async_mock_service(hass, "media_player", "play_media")
    client = await hass_client()
    rooms = await (await client.get("/api/roomcall/rooms")).json()
    assert rooms["doorbell"] == {
        "entity": "binary_sensor.front_door_visitor",
        "camera": "camera.front_door",
        "name": "Front Door",
    }

    order = []
    hass.bus.async_listen(
        "call_service",
        lambda event: (
            order.append(event.data["domain"])
            if event.data["domain"] in ("notify", "media_player")
            else None
        ),
    )
    await _press(hass)
    # The time-sensitive phone alert goes out before the rooms start playing.
    assert order[0] == "notify" and "media_player" in order
    assert _spoken(tts_audio) == ["Someone is at the Front Door."]
    assert {c.data["entity_id"] for c in played} == {
        "media_player.kitchen_sonos",
        "media_player.kitchen_hub",
    }
    # One notification: the doorbell's own, with the snapshot and Answer, no call notification.
    assert len(phone) == 1
    note = phone[0].data
    assert note["title"] == "Front Door" and note["message"] == "Someone is at the Front Door."
    assert note["data"]["image"] == "/api/camera_proxy/camera.front_door"
    assert note["data"]["actions"] == [
        {"action": "URI", "title": "Answer", "uri": "entityId:camera.front_door"}
    ]
    assert note["data"]["push"] == {"interruption-level": "time-sensitive"}
    assert hass.data[DOMAIN].metrics.counters["doorbell_rings"] == 1

    # Releasing, a bounce within the cooldown, or coming back from unavailable don't ring.
    await _press(hass, "off")
    await _press(hass)
    await _press(hass, "unavailable")
    clock[0] += 60
    await _press(hass, "on")
    assert len(phone) == 1
    await _press(hass, "off")
    await _press(hass, "on")
    assert len(phone) == 2


async def test_doorbell_without_camera_or_speakers_still_tells_phones(
    hass: HomeAssistant, house, tts_audio
):
    phone = async_mock_service(hass, "notify", "mobile_app_alex_phone")
    hass.states.async_set("event.doorbell", "unknown", {"friendly_name": "Doorbell"})
    await _setup(hass, house, doorbell_entity="event.doorbell")
    async_mock_service(hass, "media_player", "play_media")
    with patch(
        "custom_components.roomcall.messages.send",
        side_effect=CallError("no speakers", code="no_targets"),
    ):
        hass.states.async_set(
            "event.doorbell", "2026-10-22T10:00:00.000+00:00", {"friendly_name": "Doorbell"}
        )
        await hass.async_block_till_done()
    note = phone[0].data
    assert note["title"] == "Doorbell" and "actions" not in note["data"]


async def test_no_doorbell_configured(hass: HomeAssistant, house, hass_client):
    await _setup(hass, house)
    rooms = await (await (await hass_client()).get("/api/roomcall/rooms")).json()
    assert rooms["doorbell"] is None


async def test_the_doorbells_own_area_names_the_alert(hass: HomeAssistant, house, tts_audio):
    from homeassistant.helpers import area_registry as ar

    porch = ar.async_get(hass).async_create("Porch")
    registry = er.async_get(hass)
    sensor = registry.async_get_or_create(
        "binary_sensor", "test", "porch_visitor", suggested_object_id="porch_visitor"
    )
    registry.async_update_entity(sensor.entity_id, area_id=porch.id)
    hass.states.async_set("binary_sensor.porch_visitor", "off")
    phone = async_mock_service(hass, "notify", "mobile_app_alex_phone")
    # The camera sits in "Front Door"; the doorbell itself is on the porch.
    await _setup(
        hass,
        house,
        doorbell_entity="binary_sensor.porch_visitor",
        doorbell_camera="camera.front_door",
    )
    async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("binary_sensor.porch_visitor", "on")
    await hass.async_block_till_done()
    assert phone[0].data["title"] == "Porch"
