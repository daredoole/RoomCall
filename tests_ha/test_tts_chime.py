"""Real audio composition and provider failures for typed and Siri messages."""

from __future__ import annotations

import math
import struct
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.roomcall.core import TranscodeError, to_speech_pcm
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.messages import request_for
from homeassistant.core import Context
from homeassistant.helpers.network import NoURLAvailableError
from pytest_homeassistant_custom_component.common import async_mock_service


def energy(samples, frequency):
    return abs(
        sum(
            value
            * complex(
                math.cos(index * frequency * 2 * math.pi / 16000),
                math.sin(index * frequency * 2 * math.pi / 16000),
            )
            for index, value in enumerate(samples)
        )
    )


async def test_typed_and_siri_messages_have_real_chime_once_per_fanout(
    hass, setup_roomcall, house, tts_audio
):
    playback = async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("tts.piper", "unknown")
    response = await hass.services.async_call(
        "roomcall",
        "broadcast",
        {"areas": house["kitchen"], "message": "Dinner"},
        blocking=True,
        return_response=True,
    )
    assert response["ok"] and response["audible"] is None
    assert len(playback) == 2 and tts_audio.await_count == 1
    urls = [call.data["media_content_id"] for call in playback]
    assert urls[0].split("?")[0] == urls[1].split("?")[0]
    record = hass.data["roomcall"].history.records()[0]
    audio = hass.data["roomcall"].history.audio(record.call_id)
    assert audio is not None
    pcm = await to_speech_pcm("ffmpeg", audio.data)
    samples = struct.unpack("<" + "h" * (len(pcm) // 2), pcm)
    assert len(pcm) / 32000 > 1.3
    assert energy(samples[200:1500], 1047) > 5 * energy(samples[200:1500], 440)
    assert energy(samples[12000:14000], 440) > 5 * energy(samples[12000:14000], 1047)


@pytest.mark.parametrize(
    "failure", ["provider", "empty", "transcode", "timeout", "url", "forbidden"]
)
async def test_preparation_failures_are_explicit_and_play_nothing(
    hass, setup_roomcall, tts_audio, failure
):
    hass.states.async_set("tts.piper", "unknown")
    data = hass.data["roomcall"]
    if failure in {"provider", "timeout"}:
        tts_audio.side_effect = (
            TimeoutError() if failure == "timeout" else RuntimeError("private provider details")
        )
    with ExitStack() as stack:
        if failure == "empty":
            stack.enter_context(
                patch(
                    "custom_components.roomcall.messages.to_playable",
                    AsyncMock(return_value=(b"", "audio/mpeg")),
                )
            )
        elif failure == "transcode":
            stack.enter_context(
                patch(
                    "custom_components.roomcall.messages.to_playable",
                    side_effect=TranscodeError("private file"),
                )
            )
        elif failure == "url":
            stack.enter_context(
                patch(
                    "custom_components.roomcall.http.base_url",
                    side_effect=NoURLAvailableError,
                )
            )
        elif failure == "forbidden":
            stack.enter_context(
                patch(
                    "custom_components.roomcall.communication.permitted",
                    AsyncMock(return_value=False),
                )
            )
        code = {
            "url": "speaker_url_missing",
            "forbidden": "forbidden",
        }.get(failure, "tts_preparation_failed")
        with pytest.raises(Problem, match=code):
            await request_for(hass, data, {"message": "Hello"}, Context())
    if failure in {"url", "forbidden"}:
        tts_audio.assert_not_called()
    assert not data.history.records()


async def test_speaking_speed_slows_synthesized_speech_but_not_the_chime(
    hass, setup_roomcall, house, tts_audio
):
    """A slower speaking speed stretches the 1-second voice by real ffmpeg, keeping the chime."""
    async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("tts.piper", "unknown")
    data = hass.data["roomcall"]
    seconds = {}
    for rate in (1.0, 0.75):
        data.speech_rate = rate
        response = await hass.services.async_call(
            "roomcall",
            "broadcast",
            {"areas": house["kitchen"], "message": "Dinner"},
            blocking=True,
            return_response=True,
        )
        assert response["ok"]
        record = data.history.records()[0]
        audio = data.history.audio(record.call_id)
        seconds[rate] = len(await to_speech_pcm("ffmpeg", audio.data)) / 32000
    # Same message, different speed: synthesized and composed again, not served from cache.
    assert tts_audio.await_count == 2
    assert 0.25 < seconds[0.75] - seconds[1.0] < 0.45


async def test_speaking_speed_prepares_speech_even_without_a_chime(hass, setup_roomcall, tts_audio):
    hass.states.async_set("tts.piper", "unknown")
    data = hass.data["roomcall"]
    data.chime_style = None
    plain = await request_for(hass, data, {"message": "Hello"}, Context())
    assert plain.media_id.startswith("media-source://tts/tts.piper")
    data.speech_rate = 0.85
    slowed = await request_for(hass, data, {"message": "Hello"}, Context())
    assert "/api/roomcall/clip/" in slowed.media_id
    assert tts_audio.await_count == 1
