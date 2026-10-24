"""Live calls: audio streams to the speakers while the caller is still talking."""

from __future__ import annotations

import array
import asyncio
import math
import shutil

import pytest
from custom_components.roomcall.live import LiveStream
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import async_mock_service

DOMAIN = "roomcall"
FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg needed")


def _tone_frames(seconds: float, frame_ms: int = 40) -> list[bytes]:
    n = int(16000 * frame_ms / 1000)
    frames = []
    for f in range(round(seconds * 1000 / frame_ms)):
        frames.append(
            array.array(
                "h",
                (int(6000 * math.sin(2 * math.pi * 300 * (f * n + i) / 16000)) for i in range(n)),
            ).tobytes()
        )
    return frames


@needs_ffmpeg
async def test_mp3_reaches_listeners_before_the_caller_stops():
    stream = LiveStream(FFMPEG, chime="chirp")
    await stream.start()
    received: list[bytes] = []

    async def listen() -> None:
        async for chunk in stream.chunks():
            received.append(chunk)

    listener = asyncio.create_task(listen())
    frames = _tone_frames(1.6)
    for frame in frames:
        stream.feed(frame)
        await asyncio.sleep(0.04)  # real time, like a microphone
    heard_while_talking = sum(map(len, received))
    stream.end()
    await asyncio.wait_for(listener, 10)
    assert heard_while_talking > 8000  # chime plus most of the speech, before the end
    assert sum(map(len, received)) == len(stream.mp3)
    assert stream.done
    assert len(stream.pcm) == sum(map(len, frames))


@needs_ffmpeg
async def test_live_call_over_websocket(
    hass: HomeAssistant, setup_roomcall, house, hass_ws_client, hass_client_no_auth
):
    played = async_mock_service(hass, "media_player", "play_media")
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 5, "type": "roomcall/live", "target": house["basement"]})
    result = await ws.receive_json()
    assert result["success"], result
    ready = await ws.receive_json()
    assert ready["event"]["type"] == "ready"
    handler = ready["event"]["handler_id"]

    frames = _tone_frames(1.0)
    for frame in frames[:10]:
        await ws.send_bytes(bytes([handler]) + frame)
    delivered = await ws.receive_json()
    assert delivered["event"]["type"] == "delivered"
    assert delivered["event"]["ok"] is True

    # The speaker fetches the live URL while the caller is still talking.
    url = played[0].data["media_content_id"]
    assert "/api/roomcall/live/" in url
    speaker = await hass_client_no_auth()
    response = await speaker.get(url.removeprefix("http://ha.local:8123"))
    assert response.status == 200
    assert response.headers["Content-Type"] == "audio/mpeg"
    first = await response.content.read(1024)
    assert first

    for frame in frames[10:]:
        await ws.send_bytes(bytes([handler]) + frame)
    await ws.send_bytes(bytes([handler]))  # empty frame: the caller let go
    rest = await response.read()
    done = await ws.receive_json()
    assert done["event"]["type"] == "done"
    assert done["event"]["seconds"] == 1.0
    record = hass.data[DOMAIN].history.records()[0]
    assert record.has_audio
    assert len(first) + len(rest) > 10000


async def test_live_call_refused_by_do_not_disturb(
    hass: HomeAssistant, setup_roomcall, house, hass_ws_client
):
    hass.data[DOMAIN].dnd = True
    ws = await hass_ws_client(hass)
    await ws.send_json({"id": 7, "type": "roomcall/live", "target": house["basement"]})
    result = await ws.receive_json()
    assert not result["success"]
    assert result["error"]["code"] == "call_refused"
    assert "Do Not Disturb" in result["error"]["message"]


async def test_unknown_live_stream_is_404(
    hass: HomeAssistant, setup_roomcall, hass_client, hass_client_no_auth
):
    assert (await (await hass_client()).get("/api/roomcall/live/nope.mp3")).status == 404
    # Live call audio is never served without a login or a signed path.
    assert (await (await hass_client_no_auth()).get("/api/roomcall/live/nope.mp3")).status == 401


@needs_ffmpeg
async def test_acceptance_receipt_precedes_transcription_and_survives_disconnect(
    hass, setup_roomcall, house, hass_ws_client
):
    from unittest.mock import AsyncMock, patch

    data = hass.data[DOMAIN]
    data.stt_engine = "stt.example"
    started = asyncio.Event()
    release = asyncio.Event()

    async def transcribe(*args):
        started.set()
        await release.wait()
        return "Recorded message"

    async_mock_service(hass, "media_player", "play_media")
    with (
        patch("custom_components.roomcall.calls._transcribe_pcm", side_effect=transcribe),
        patch("custom_components.roomcall.scheduler.wait_for_playback", new=AsyncMock()),
    ):
        ws = await hass_ws_client(hass)
        await ws.send_json({"id": 5, "type": "roomcall/live", "target": house["basement"]})
        assert (await ws.receive_json())["success"]
        handler = (await ws.receive_json())["event"]["handler_id"]
        frames = _tone_frames(1.0)
        for frame in frames:
            await ws.send_bytes(bytes([handler]) + frame)
        await ws.send_bytes(bytes([handler]))
        receipt = (await ws.receive_json())["event"]
        assert receipt["type"] == "delivered" and receipt["ok"]
        assert receipt["audible"] is None
        await asyncio.wait_for(started.wait(), 5)
        assert not data.history.records()  # Postprocessing is still waiting.
        await ws.close()  # The app can finish on acceptance without losing the recording.
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
        record = data.history.records()[0]
        assert record.call_id == receipt["id"] and record.transcript == "Recorded message"
        assert record.has_audio and record.ok
        stream = next(iter(data.live._streams.values()))
        assert len(stream.pcm) == sum(map(len, frames))
