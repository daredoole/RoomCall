"""Tests for chimes, delivery, quiet hours, priority, people, history, replies and transcripts."""

from __future__ import annotations

import io
import wave
from datetime import datetime, time
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
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


async def _setup(hass: HomeAssistant, **options) -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, data={"speakers": SPEAKERS, "volume": 0.4}, options={})
    entry.add_to_hass(hass)
    if options:
        hass.config_entries.async_update_entry(
            entry, options={"speakers": SPEAKERS, "volume": 0.4, **options}
        )
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


# --- chimes ---------------------------------------------------------------------------


@pytest.mark.parametrize("style", ["chirp", "ding", "double", "rising", "doorbell"])
async def test_every_chime_style_renders(hass: HomeAssistant, house, hass_client_no_auth, style):
    await _setup(hass)
    client = await hass_client_no_auth()
    resp = await client.get(f"/api/roomcall/chime/{style}.mp3")
    assert resp.status == 200
    audio = await resp.read()
    assert len(audio) > 1000


async def test_unknown_chime_is_404(hass: HomeAssistant, house, hass_client_no_auth):
    await _setup(hass)
    client = await hass_client_no_auth()
    assert (await client.get("/api/roomcall/chime/nope.mp3")).status == 404


async def test_talk_with_each_chime_option(hass: HomeAssistant, house, hass_client):
    await _setup(hass, chime_options={"style": "double"})
    async_mock_service(hass, "media_player", "play_media")
    client = await hass_client()
    resp = await client.post(
        f"/api/roomcall/talk?target={house['basement']}",
        data=_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    body = await resp.json()
    assert resp.status == 200, body
    assert body["seconds"] > 0.5


async def test_chime_off_passes_wav_through(hass: HomeAssistant, house, hass_client):
    await _setup(hass, chime_options={"style": "off"})
    calls = async_mock_service(hass, "media_player", "play_media")
    client = await hass_client()
    resp = await client.post(
        f"/api/roomcall/talk?target={house['basement']}",
        data=_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    assert resp.status == 200
    assert calls[0].data["media_content_id"].split("?", 1)[0].endswith(".wav")


# --- delivery -------------------------------------------------------------------------


async def test_speakers_are_staggered(hass: HomeAssistant, house):
    await _setup(hass, delivery={"stagger_ms": 200, "retry": False, "urgent_volume": 0.8})
    started: list[tuple[str, float]] = []

    async def play(call: ServiceCall) -> None:
        started.append((call.data["entity_id"], hass.loop.time()))

    hass.services.async_register("media_player", "play_media", play)
    await hass.services.async_call(
        DOMAIN, "broadcast", {"media_url": "http://x/a.mp3"}, blocking=True
    )
    times = [t for _, t in started]
    assert len(times) == 3
    assert times[1] - times[0] >= 0.18
    assert times[2] - times[1] >= 0.18


async def test_failed_speaker_is_retried_once(hass: HomeAssistant, house):
    await _setup(hass, delivery={"stagger_ms": 0, "retry": True, "urgent_volume": 0.8})
    attempts: dict[str, int] = {}

    async def play(call: ServiceCall) -> None:
        entity = call.data["entity_id"]
        attempts[entity] = attempts.get(entity, 0) + 1
        if entity == "media_player.basement_sonos" and attempts[entity] == 1:
            raise RuntimeError("busy")

    hass.services.async_register("media_player", "play_media", play)
    with patch("custom_components.roomcall.core.RETRY_DELAY_SECONDS", 0):
        result = await hass.services.async_call(
            DOMAIN,
            "broadcast",
            {"areas": [house["basement"]], "media_url": "http://x/a.mp3"},
            blocking=True,
            return_response=True,
        )
    assert result["ok"] is True
    assert result["results"][0]["attempts"] == 2


# --- Do Not Disturb, quiet hours, priority ---------------------------------------------


async def test_do_not_disturb_blocks_normal_but_not_urgent(hass: HomeAssistant, house):
    await _setup(hass)
    calls = async_mock_service(hass, "media_player", "play_media")
    switch = er.async_get(hass).async_get_entity_id(
        "switch",
        DOMAIN,
        next(e.unique_id for e in er.async_get(hass).entities.values() if e.domain == "switch"),
    )
    await hass.services.async_call("switch", "turn_on", {"entity_id": switch}, blocking=True)
    assert hass.states.get(switch).state == "on"
    with pytest.raises(ServiceValidationError, match="Do Not Disturb"):
        await hass.services.async_call(
            DOMAIN, "broadcast", {"media_url": "http://x/a.mp3"}, blocking=True
        )
    assert not calls
    await hass.services.async_call(
        DOMAIN,
        "broadcast",
        {"media_url": "http://x/a.mp3", "priority": "urgent"},
        blocking=True,
    )
    assert len(calls) == 3
    assert {c.data["extra"]["volume"] for c in calls} == {80}


async def test_quiet_hours_skip_rooms_and_lower_volume(hass: HomeAssistant, house):
    await _setup(
        hass,
        quiet_hours={
            "start": "22:00:00",
            "end": "07:00:00",
            "volume": 0.15,
            "skip_areas": [house["basement"]],
        },
    )
    calls = async_mock_service(hass, "media_player", "play_media")
    night = dt_util.as_local(datetime(2026, 10, 20, 23, 30)).replace(
        tzinfo=dt_util.get_default_time_zone()
    )
    with patch("custom_components.roomcall.calls.dt_util.now", return_value=night):
        result = await hass.services.async_call(
            DOMAIN,
            "broadcast",
            {"media_url": "http://x/a.mp3"},
            blocking=True,
            return_response=True,
        )
    assert sorted(c.data["entity_id"] for c in calls) == [
        "media_player.kitchen_hub",
        "media_player.kitchen_sonos",
    ]
    assert {c.data["extra"]["volume"] for c in calls} == {15}
    assert result["skipped"] == {"Basement_Sonos": "quiet hours"}


def test_quiet_hours_window():
    from custom_components.roomcall.policy import QuietHours

    overnight = QuietHours(start=time(22), end=time(7))
    assert overnight.active(datetime(2026, 1, 1, 23, 0))
    assert overnight.active(datetime(2026, 1, 1, 6, 59))
    assert not overnight.active(datetime(2026, 1, 1, 12, 0))
    daytime = QuietHours(start=time(13), end=time(15))
    assert daytime.active(datetime(2026, 1, 1, 14, 0))
    assert not daytime.active(datetime(2026, 1, 1, 16, 0))
    assert not QuietHours().active(datetime(2026, 1, 1, 0, 0))


# --- people ---------------------------------------------------------------------------


async def test_call_a_person_in_their_room(hass: HomeAssistant, house):
    await _setup(hass)
    calls = async_mock_service(hass, "media_player", "play_media")
    registry = er.async_get(hass)
    from homeassistant.helpers import device_registry as dr

    config_entry = MockConfigEntry(domain="bermuda")
    config_entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=config_entry.entry_id, identifiers={("bermuda", "phone")}
    )
    tracker = registry.async_get_or_create(
        "device_tracker",
        "bermuda",
        "phone_tracker",
        device_id=device.id,
        suggested_object_id="alex_phone",
    )
    area_sensor = registry.async_get_or_create(
        "sensor",
        "bermuda",
        "phone_area",
        device_id=device.id,
        suggested_object_id="alex_phone_area",
    )
    hass.states.async_set(tracker.entity_id, "home")
    hass.states.async_set(area_sensor.entity_id, "Basement")
    hass.states.async_set(
        "person.alex", "home", {"device_trackers": [tracker.entity_id], "friendly_name": "Alex"}
    )

    await hass.services.async_call(
        DOMAIN,
        "broadcast",
        {"people": ["person.alex"], "media_url": "http://x/a.mp3"},
        blocking=True,
    )
    assert [c.data["entity_id"] for c in calls] == ["media_player.basement_sonos"]

    hass.states.async_set(area_sensor.entity_id, "unknown")
    with pytest.raises(ServiceValidationError, match="Alex"):
        await hass.services.async_call(
            DOMAIN,
            "broadcast",
            {"people": ["person.alex"], "media_url": "http://x/a.mp3"},
            blocking=True,
        )


# --- history --------------------------------------------------------------------------


async def test_history_and_replay(hass: HomeAssistant, house, hass_client, hass_client_no_auth):
    await _setup(hass)
    async_mock_service(hass, "media_player", "play_media")
    client = await hass_client()
    resp = await client.post(
        f"/api/roomcall/talk?target={house['basement']}&from={house['kitchen']}",
        data=_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    call_id = (await resp.json())["id"]
    history = await (await client.get("/api/roomcall/history")).json()
    latest = history["calls"][0]
    assert latest["id"] == call_id
    assert latest["from"] == house["kitchen"]
    assert latest["has_audio"] is True
    replay = await client.get(f"/api/roomcall/history/{call_id}.mp3")
    assert replay.status == 200
    assert len(await replay.read()) > 1000
    anon = await hass_client_no_auth()
    assert (await anon.get(f"/api/roomcall/history/{call_id}.mp3")).status == 401
    sensor = hass.states.get("sensor.roomcall_last_call")
    assert sensor.attributes["history"][0]["id"] == call_id


async def test_history_survives_option_reload(hass: HomeAssistant, house):
    entry = await _setup(hass)
    async_mock_service(hass, "media_player", "play_media")
    await hass.services.async_call(
        DOMAIN, "broadcast", {"media_url": "http://x/a.mp3"}, blocking=True
    )
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert len(hass.data[DOMAIN].history.records()) == 1


# --- notifications, reply and transcripts ----------------------------------------------


async def test_notify_with_reply_skips_the_caller(
    hass: HomeAssistant, house, hass_client, hass_admin_user
):
    for name, user in (("Alex Phone", hass_admin_user.id), ("Sam Phone", "someone-else")):
        MockConfigEntry(
            domain="mobile_app", data={"device_name": name, "user_id": user}
        ).add_to_hass(hass)
    alex = async_mock_service(hass, "notify", "mobile_app_alex_phone")
    sam = async_mock_service(hass, "notify", "mobile_app_sam_phone")
    await _setup(
        hass,
        replies={
            "notify": ["notify.mobile_app_alex_phone", "notify.mobile_app_sam_phone"],
            "dashboard_path": "/home-hub/home",
        },
    )
    async_mock_service(hass, "media_player", "play_media")
    client = await hass_client()
    await client.post(
        f"/api/roomcall/talk?target={house['basement']}&from={house['kitchen']}",
        data=_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    await hass.async_block_till_done()
    assert not alex  # the caller's own phone
    assert len(sam) == 1
    data = sam[0].data
    assert data["message"] == "Kitchen → Basement"
    action = data["data"]["actions"][0]
    assert action["title"] == "Reply"
    assert action["uri"] == f"/home-hub/home?roomcall={house['kitchen']}"


async def test_transcript_goes_to_history_and_notification(hass: HomeAssistant, house, hass_client):
    from homeassistant.components import stt

    MockConfigEntry(
        domain="mobile_app", data={"device_name": "Sam Phone", "user_id": "x"}
    ).add_to_hass(hass)
    sam = async_mock_service(hass, "notify", "mobile_app_sam_phone")
    await _setup(
        hass, replies={"notify": ["notify.mobile_app_sam_phone"], "stt_engine": "stt.fake"}
    )
    async_mock_service(hass, "media_player", "play_media")
    engine = AsyncMock()
    engine.supported_languages = ["en"]
    engine.internal_async_process_audio_stream.return_value = stt.SpeechResult(
        "dinner is ready", stt.SpeechResultState.SUCCESS
    )
    with patch("homeassistant.components.stt.async_get_speech_to_text_entity", return_value=engine):
        client = await hass_client()
        resp = await client.post(
            f"/api/roomcall/talk?target={house['basement']}",
            data=_wav(),
            headers={"Content-Type": "audio/wav"},
        )
        body = await resp.json()
    await hass.async_block_till_done()
    assert body["transcript"] == "dinner is ready"
    assert hass.data[DOMAIN].history.records()[0].transcript == "dinner is ready"
    assert "dinner is ready" in sam[0].data["message"]


# --- options flow ---------------------------------------------------------------------


async def test_options_flow_sections(hass: HomeAssistant, house):
    entry = await _setup(hass)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] == "form"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "speakers": SPEAKERS,
            "gain_db": 0,
            "speech_rate": 0.85,
            "chime_options": {"style": "rising"},
            "delivery": {"stagger_ms": 300, "retry": True, "urgent_volume": 0.9},
            "quiet_hours": {"start": "22:00:00", "end": "07:00:00"},
            "replies": {},
        },
    )
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()
    data = hass.data[DOMAIN]
    assert data.chime_style == "rising"
    assert data.speech_rate == 0.85
    assert data.stagger_ms == 300
    assert data.urgent_volume == 0.9
    assert data.quiet.start == time(22)
