"""Boundaries, resource cleanup and recoverable failures across the integration."""

from __future__ import annotations

import asyncio
import time
from datetime import time as clock_time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from custom_components.roomcall import core, policy, restore
from custom_components.roomcall.calls import CallError, CallRequest, _limited_volume, place_call
from custom_components.roomcall.capabilities import can_play, deduplicate, physical_key
from custom_components.roomcall.communication import execute
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.messages import request_for
from custom_components.roomcall.scheduler import wait_for_playback
from custom_components.roomcall.stations import Station, Stations
from custom_components.roomcall.telemetry import Evidence, Metrics
from custom_components.roomcall.voicemail import Mailbox
from homeassistant.components.media_player import MediaPlayerEntityFeature as F
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import intent
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_mock_service


def test_bounded_clip_and_history_retention():
    clips = core.ClipStore(ttl=10, max_clips=1)
    first = clips.add(b"a", "audio/mpeg")
    second = clips.add(b"b", "audio/mpeg")
    assert clips.get(first) is None and clips.get(second).data == b"b"
    with patch(
        "custom_components.roomcall.core.time.monotonic", return_value=time.monotonic() + 11
    ):
        assert clips.get(second) is None
    history = core.History(size=1)
    old, new = core.CallRecord("old", "message", []), core.CallRecord("new", "message", [])
    history.add(old, b"audio")
    history.add(new)
    assert history.records() == [new]
    assert history.audio(old.call_id) is None and history.audio(new.call_id) is None


async def test_registry_fallback_aliases_disabled_and_capabilities(hass, setup_roomcall, house):
    devices, registry = dr.async_get(hass), er.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=setup_roomcall.entry_id,
        identifiers={("test", "speaker")},
        connections={("mac", "AA:BB:CC:DD:EE:FF")},
    )
    devices.async_update_device(device.id, area_id=house["kitchen"])
    entity = registry.async_get_or_create(
        "media_player", "test", "mac_speaker", device_id=device.id, original_name="MAC Speaker"
    )
    assert core.speaker_area(hass, entity.entity_id) == house["kitchen"]
    assert physical_key(hass, entity.entity_id, {}) == "mac:aa:bb:cc:dd:ee:ff"
    assert core.speaker_area(hass, "media_player.kitchen_sonos") == house["kitchen"]
    no_area = registry.async_get_or_create(
        "media_player", "test", "no_area", original_name="No area"
    )
    assert core.speaker_area(hass, no_area.entity_id) is None
    ar.async_get(hass).async_update(house["kitchen"], aliases={"Cookery"})
    assert core.area_id_for(hass, "COOKERY") == house["kitchen"]
    registry.async_update_entity(entity.entity_id, disabled_by=er.RegistryEntryDisabler.USER)
    assert deduplicate(hass, [entity.entity_id], {}) == ([], {entity.entity_id: "disabled"})
    assert not can_play(hass, "media_player.missing")
    hass.states.async_set("media_player.capable", "idle", {"supported_features": F.PLAY_MEDIA})
    assert can_play(hass, "media_player.capable")


async def test_presence_cycle_missing_tracker_and_explicit_area(hass, house):
    assert policy.person_area(hass, "person.missing") is None
    hass.states.async_set(
        "person.alex", "home", {"device_trackers": ["person.alex", "device_tracker.missing"]}
    )
    assert policy.person_area(hass, "person.alex") is None
    hass.states.async_set("device_tracker.phone", "home", {"area": "Kitchen"})
    hass.states.async_set("person.alex", "home", {"device_trackers": ["device_tracker.phone"]})
    assert policy.person_area(hass, "person.alex") == house["kitchen"]
    quiet = policy.QuietHours(clock_time(0), clock_time(23, 59), skip_areas=[house["kitchen"]])
    with pytest.raises(policy.DoNotDisturbError, match="muted"):
        policy.plan(
            hass,
            ["media_player.kitchen_sonos"],
            base_volume=None,
            priority="normal",
            dnd=False,
            quiet=quiet,
            urgent_volume=0.8,
            now=dt_util.now().replace(hour=12),
        )


async def test_message_provider_errors_and_chime_language(hass, setup_roomcall):
    data = hass.data["roomcall"]
    with pytest.raises(CallError, match="text-to-speech"):
        await request_for(hass, data, {"message": "Hello"}, Context())
    with pytest.raises(Problem, match="content_required"):
        await request_for(hass, data, {}, Context())
    data.translation_entity = "ai_task.translator"
    with (
        patch.object(type(hass.services), "async_call", AsyncMock(return_value={"data": ""})),
        pytest.raises(Problem, match="translation_failed"),
    ):
        await request_for(
            hass, data, {"message": "Hello", "translate": True, "language": "es"}, Context()
        )
    hass.states.async_set("tts.piper", "unknown")
    data.chime_url = "http://ha.local/chime.mp3"
    with patch(
        "custom_components.roomcall.messages.to_playable",
        AsyncMock(return_value=(b"audio", "audio/mpeg")),
    ) as prepare:
        request = await request_for(hass, data, {"message": "Hola", "language": "es"}, Context())
    assert request.audio == b"audio" and request.language == "es"
    assert prepare.call_args.args[5] == "http://ha.local/chime.mp3"


async def test_controls_not_ready_whitespace_and_provider_error(hass):
    assert (await execute(hass, "discover", {}, Context()))["error"]["code"] == "not_ready"
    assert (await execute(hass, "send", {"message": "   "}, Context()))["error"][
        "code"
    ] == "content_required"


async def test_service_registration_errors_and_intent_failures(hass, setup_roomcall):
    result = await hass.services.async_call(
        "roomcall", "communicate", {"operation": "discover"}, blocking=True, return_response=True
    )
    assert result["ok"]
    hass.bus.async_fire("mobile_app_notification_action", {"action": "UNRELATED"})
    await hass.async_block_till_done()
    with pytest.raises(intent.IntentHandleError):
        await intent.async_handle(
            hass,
            "test",
            "RoomCallAnnounce",
            {"message": {"value": "Hello"}, "area": {"value": "missing"}},
        )
    with (
        patch(
            "custom_components.roomcall.intents.validate_targets", side_effect=Problem("forbidden")
        ),
        pytest.raises(intent.IntentHandleError),
    ):
        await intent.async_handle(hass, "test", "RoomCallAnnounce", {"message": {"value": "Hello"}})
    with patch.object(type(hass.services), "async_call", AsyncMock(return_value={"results": []})):
        response = await intent.async_handle(
            hass, "test", "RoomCallAnnounce", {"message": {"value": "Hello"}}
        )
        assert "failed" in response.speech["plain"]["speech"].lower()
    with patch(
        "custom_components.roomcall.communication.discover", side_effect=RuntimeError("provider")
    ):
        result = await execute(hass, "discover", {}, Context())
        assert result["error"]["code"] == "provider_failed"
    with (
        patch("custom_components.roomcall.communication.discover", side_effect=TimeoutError),
        pytest.raises(TimeoutError),
    ):
        await execute(hass, "discover", {}, Context())
    data = hass.data.pop("roomcall")
    try:
        with pytest.raises(ServiceValidationError, match="not set up"):
            await hass.services.async_call(
                "roomcall", "broadcast", {"message": "Hello"}, blocking=True
            )
    finally:
        hass.data["roomcall"] = data


async def test_scheduler_limits_cancellation_and_live_wait(hass, setup_roomcall):
    data = hass.data["roomcall"]
    scheduler = data.scheduler
    scheduler.pending = 128
    with pytest.raises(RuntimeError, match="queue_full"):
        await scheduler.acquire("media_player.kitchen_sonos", None)
    result = await core._play_one(hass, "media_player.kitchen_sonos", "http://clip", None)
    assert result.error_code == "queue_full"
    scheduler.pending = 0
    with patch.object(scheduler, "acquire", side_effect=TimeoutError):
        result = await core._play_one(hass, "media_player.kitchen_sonos", "http://clip", None)
        assert result.error_code == "expired"
    lock = asyncio.Lock()
    await lock.acquire()
    with patch.object(scheduler, "acquire", AsyncMock(return_value=lock)):
        result = await core._play_one(
            hass, "media_player.kitchen_sonos", "http://clip", None, deadline=0
        )
        assert result.error_code == "expired" and not lock.locked()
    with (
        patch("custom_components.roomcall.restore.wait_until_done", AsyncMock()) as wait,
        patch("custom_components.roomcall.scheduler.asyncio.sleep", AsyncMock()),
    ):
        until = AsyncMock()
        await wait_for_playback(hass, "media_player.kitchen_sonos", 1, until)
        until.assert_awaited_once()
        wait.assert_awaited_once()
    await lock.acquire()
    # Wait until the task actually starts; canceling a never-started task cannot run finally.
    entered = asyncio.Event()

    async def finish(*args, **kwargs):
        entered.set()
        await asyncio.Event().wait()

    guard = SimpleNamespace(finish=finish)
    scheduler.finish(lock, "media_player.kitchen_sonos", guard, None, None)
    await entered.wait()
    await scheduler.close()
    assert not lock.locked()


async def test_evidence_and_mailbox_retention_caps(hass, setup_roomcall):
    evidence = Evidence(Metrics())
    record = core.CallRecord("Kitchen", "media", [])
    for _ in range(2049):
        evidence.url(
            hass, "http://ha/api/roomcall/clip/a.mp3", record, "media_player.kitchen_sonos"
        )
    assert len(evidence.tokens) == 2048
    evidence.fetched("missing", "/api/roomcall/clip/a.mp3")
    for _ in range(2049):
        evidence.watch(hass, record, "media_player.kitchen_sonos", "http://clip")
    assert len(evidence.watchers) == 2048
    evidence.close()
    assert not evidence.watchers
    mailbox = Mailbox(hass, "bounded", True, 60)
    with patch.object(mailbox.store, "async_save", AsyncMock()):
        first = await mailbox.add(None, "first", "Kitchen")
        for _ in range(20):
            await mailbox.add(None, "next", "Kitchen")
    assert len(mailbox.items) == 20 and first not in mailbox.items
    mailbox.close()


async def test_station_limits_membership_hangup_and_unknown_action(hass, house):
    stations = Stations(hass)
    for index in range(64):
        stations.register(Station(str(index), "owner", house["kitchen"], str(index), Mock()))
    with pytest.raises(Problem, match="queue_full"):
        stations.register(Station("overflow", "owner", house["kitchen"], "x", Mock()))
    with pytest.raises(Problem, match="station_unavailable"):
        stations.owned("missing", "owner")
    session = stations.invite("0", "1", "owner")
    with pytest.raises(Problem, match="forbidden"):
        stations.action("2", "owner", session, "answer")
    with pytest.raises(Problem, match="invalid_request"):
        stations.action("0", "owner", session, "unknown")
    stations.action("0", "owner", session, "hangup")
    assert not stations.sessions
    stations.close()


async def test_recorded_pipeline_conversion_url_and_transcript_failures(hass, setup_roomcall):
    from custom_components.roomcall import calls

    data = hass.data["roomcall"]
    with (
        patch.object(calls, "to_playable", side_effect=core.TranscodeError("bad audio")),
        pytest.raises(CallError, match="convert recording"),
    ):
        await calls.place_recorded_call(hass, data, b"bad", "audio/webm", targets=["kitchen"])
    with (
        patch.object(calls, "to_playable", AsyncMock(return_value=(b"mp3", "audio/mpeg"))),
        patch.object(calls, "get_url", side_effect=NoURLAvailableError),
        pytest.raises(CallError, match="internal URL"),
    ):
        await calls.place_recorded_call(hass, data, b"bad", "audio/webm", targets=["kitchen"])
    data.stt_engine = "stt.test"
    with patch.object(calls, "transcribe", AsyncMock(return_value="Hello")):
        assert await calls._transcribe_pcm(hass, data, b"pcm") == "Hello"
    with (
        patch.object(calls, "transcribe", AsyncMock(side_effect=RuntimeError("offline"))),
        patch.object(calls, "to_speech_pcm", AsyncMock(return_value=b"pcm")),
    ):
        assert await calls._transcribe_pcm(hass, data, b"pcm") is None
        assert (
            await calls._transcript(
                hass, data, CallRequest(["kitchen"], "http://clip", "talk", recording=b"audio")
            )
            is None
        )


async def test_call_callbacks_permissions_live_transcript_and_failed_voicemail(
    hass, setup_roomcall
):
    from custom_components.roomcall import calls

    data = hass.data["roomcall"]
    request = CallRequest(["kitchen"], "http://clip", "talk")
    with (
        patch("custom_components.roomcall.communication.permitted", AsyncMock(return_value=False)),
        pytest.raises(CallError, match="forbidden"),
    ):
        await place_call(hass, data, request)
    hass.states.async_set("media_player.kitchen_sonos", "idle", {"volume_level": "bad"})
    assert _limited_volume(hass, "media_player.kitchen_sonos", None, 0.6) == 0.6
    async_mock_service(hass, "media_player", "play_media")
    callback = Mock()
    request.on_delivered = callback
    data.notify = ["notify.phone"]
    with patch.object(calls, "notify_call", AsyncMock(side_effect=RuntimeError("offline"))):
        record = await place_call(hass, data, request)
    callback.assert_called_once_with(record)
    data.stt_engine = "stt.test"
    request.live = SimpleNamespace(mp3=b"mp3", pcm=b"pcm", wait_done=AsyncMock())
    with patch.object(calls, "_transcribe_pcm", AsyncMock(return_value="Hello")):
        record = await place_call(hass, data, request)
    assert record.transcript == "Hello"
    data.retry, data.voicemail_enabled, data.mailbox.enabled = False, True, True
    request.live, request.audio = None, b"mp3"

    async def fail(call):
        raise HomeAssistantError("offline")

    hass.services.async_register("media_player", "play_media", fail)
    record = await place_call(hass, data, request)
    assert not record.ok and len(data.mailbox.list(None)) == 1


async def test_transcription_stream_chunks_language_and_failure(hass):
    from custom_components.roomcall import replies
    from homeassistant.components import stt

    with patch.object(replies.stt, "async_get_speech_to_text_entity", return_value=None):
        assert await replies.transcribe(hass, "missing", b"pcm") is None
    chunks = []

    async def process(metadata, stream):
        assert metadata.language == "es"
        chunks.extend([chunk async for chunk in stream])
        return SimpleNamespace(result=stt.SpeechResultState.ERROR, text=None)

    engine = SimpleNamespace(
        supported_languages=["es"], internal_async_process_audio_stream=process
    )
    with patch.object(replies.stt, "async_get_speech_to_text_entity", return_value=engine):
        assert await replies.transcribe(hass, "stt.test", b"x" * 6401) is None
    assert [len(c) for c in chunks] == [3200, 3200, 1]
    calls = async_mock_service(hass, "notify", "phone")
    record = core.CallRecord("kitchen", "message", [], priority="urgent")
    assert await replies.notify_call(
        hass,
        record,
        services=["notify.missing", "notify.phone"],
        dashboard_path=None,
        caller_user_id=None,
        room_names={},
    ) == ["phone"]
    await hass.async_block_till_done()
    assert calls[0].data["data"]["push"]["interruption-level"] == "time-sensitive"


async def test_raw_fanout_restores_without_a_runtime_scheduler(hass, house):
    async_mock_service(hass, "media_player", "play_media")
    finished = AsyncMock()
    with patch("custom_components.roomcall.restore.Guard.finish", finished):
        record = await core.fan_out(
            hass,
            ["media_player.missing"],
            "http://clip",
            None,
            target="Kitchen",
            kind="media",
            restore=True,
        )
        await hass.async_block_till_done(wait_background_tasks=True)
    assert record.ok
    finished.assert_awaited_once()


async def test_restoration_missing_devices_unavailable_and_volume_failure(hass, house):
    from custom_components.roomcall.playback import identities

    assert identities(hass, "media_player.missing") == {"entity:media_player.missing"}
    assert restore.related_players(hass, "media_player.kitchen_sonos") == [
        "media_player.kitchen_sonos"
    ]
    assert restore.take(hass, ["media_player.missing"]) == []
    snapshot = restore.Snapshot("media_player.missing", None, "idle", None, 0.4, None)
    await restore._restore_one(hass, snapshot)
    hass.states.async_set(
        "media_player.volume", "idle", {"supported_features": F.VOLUME_SET, "volume_level": 0.8}
    )
    snapshot.entity_id = "media_player.volume"
    calls = async_mock_service(hass, "media_player", "volume_set")
    await restore._restore_one(hass, snapshot)
    assert calls[0].data["volume_level"] == 0.4
    with patch.object(restore, "_restore_one", AsyncMock(side_effect=RuntimeError("offline"))):
        await restore.restore(hass, [snapshot])


async def test_room_notify_reports_policy_failure(hass, setup_roomcall):
    hass.data["roomcall"].dnd = True
    with pytest.raises(HomeAssistantError, match="could not speak"):
        await hass.services.async_call(
            "notify",
            "send_message",
            {"entity_id": "notify.roomcall_kitchen", "message": "Hello"},
            blocking=True,
        )


def test_empty_voice_frame_has_zero_energy():
    from custom_components.roomcall.talkstream import Segmenter

    assert Segmenter.rms(b"") == 0
