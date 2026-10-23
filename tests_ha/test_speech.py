"""Speech latency improvements keep preparation separate from playback evidence."""

import asyncio
from unittest.mock import AsyncMock

import pytest
from custom_components.roomcall.communication import execute
from custom_components.roomcall.config_flow import validate_communication
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.messages import request_for
from custom_components.roomcall.schema import vol
from custom_components.roomcall.speech import SpeechCache
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import async_mock_service


async def test_preparation_reuse_profiles_and_permission_boundary(
    hass, setup_roomcall, tts_audio, hass_admin_user
):
    data = hass.data["roomcall"]
    hass.states.async_set("tts.piper", "unknown")
    data.speech_profiles = {"fast": {"engine": "tts.piper", "voice": "en_US-lessac-medium"}}
    assert (await execute(hass, "discover", {}, Context()))["speech_profiles"] == ["fast"]
    values = {"areas": ["kitchen"], "message": "Dinner", "speech_profile": "fast"}
    played = async_mock_service(hass, "media_player", "play_media")
    a, b = await asyncio.gather(*(request_for(hass, data, values, Context()) for _ in range(2)))
    assert a.audio == b.audio and a.media_id != b.media_id
    assert tts_audio.await_count == 1 and not played
    assert "voice=en_US-lessac-medium" in tts_audio.call_args.args[1]
    assert data.metrics.counters["speech_cache_hits"] == 1
    await request_for(hass, data, values, Context(user_id=hass_admin_user.id))
    assert tts_audio.await_count == 2  # audio does not cross user ownership
    data.speech_profiles["fast"]["voice"] = "en_US-lessac-high"
    await request_for(hass, data, values, Context())
    assert tts_audio.await_count == 3
    with pytest.raises(Problem, match="speech_profile_missing"):
        await request_for(hass, data, {**values, "speech_profile": "quality"}, Context())
    data.chime_style = None
    result = await execute(
        hass, "speech", {"areas": ["kitchen"], "message": "Prepare only"}, Context()
    )
    assert result["prepared"] and result["bytes"] > 0 and result["audible"] is None and not played
    missing = await execute(hass, "speech", {"areas": ["kitchen"]}, Context())
    assert missing["error"]["code"] == "content_required"
    assert not (await execute(hass, "speech", {"action": "clear"}, Context()))["ok"]
    assert (
        await execute(hass, "speech", {"action": "clear"}, Context(user_id=hass_admin_user.id))
    )["ok"]
    assert not data.speech_cache.items
    validate_communication({"speech_profiles": data.speech_profiles})
    with pytest.raises(vol.Invalid):
        validate_communication({"speech_profiles": {"fast": {"engine": "tts.piper"}}})


async def test_cache_expiry_limits_clear_in_flight_and_failed_provider():
    cache = SpeechCache()
    build = AsyncMock(return_value=(b"audio", "audio/mpeg"))
    await cache.prepare("first", build)
    timestamp, audio, content_type = cache.items["first"]
    cache.items["first"] = (timestamp - 601, audio, content_type)
    assert not (await cache.prepare("first", build))[2]
    for i in range(33):
        await cache.prepare(str(i), build)
    assert len(cache.items) == 32 and "first" not in cache.items
    cache.clear()
    big = AsyncMock(return_value=(b"x" * (4 * 1024 * 1024), "audio/mpeg"))
    for i in range(5):
        await cache.prepare(str(i), big)
    assert len(cache.items) == 4
    await cache.prepare(
        "oversized", AsyncMock(return_value=(b"x" * (4 * 1024 * 1024 + 1), "audio/mpeg"))
    )
    assert "oversized" not in cache.items

    async def clear_during_build():
        cache.clear()
        return b"old", "audio/mpeg"

    await cache.prepare("cleared", clear_during_build)
    assert not cache.items
    with pytest.raises(ValueError):
        await cache.prepare("failed", AsyncMock(side_effect=ValueError("provider")))
    assert "failed" not in cache.items


async def test_unrelated_preparation_is_independent_and_pending_keys_are_bounded():
    cache = SpeechCache()
    gate = asyncio.Event()

    async def waiting():
        await gate.wait()
        return b"audio", "audio/mpeg"

    tasks = [asyncio.create_task(cache.prepare(str(i), waiting)) for i in range(32)]
    await asyncio.sleep(0)
    with pytest.raises(Problem, match="queue_full"):
        await cache.prepare("overflow", waiting)
    same = asyncio.create_task(cache.prepare("0", waiting))
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*tasks, same)
    assert not cache.locks and same.result()[2]


async def test_last_tts_timestamp_does_not_invalidate_prepared_speech(
    hass, setup_roomcall, tts_audio
):
    data = hass.data["roomcall"]
    hass.states.async_set("tts.piper", "unknown")
    audio = tts_audio.return_value

    async def synthesize(*args):
        hass.states.async_set("tts.piper", "2026-10-23T14:00:00Z")
        return audio

    tts_audio.side_effect = synthesize
    values = {"areas": ["kitchen"], "message": "Doorbell"}
    await request_for(hass, data, values, Context())
    await request_for(hass, data, values, Context())
    assert tts_audio.await_count == 1
