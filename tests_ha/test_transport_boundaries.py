"""Streaming, upload and WebSocket errors, using real ffmpeg for audio paths."""

from __future__ import annotations

import asyncio
import io
import shutil
import wave
from datetime import timedelta
from unittest.mock import AsyncMock, Mock, patch

import pytest
from custom_components.roomcall import core, ws
from custom_components.roomcall.calls import CallError
from custom_components.roomcall.live import MAX_SECONDS, LiveStore, LiveStream
from homeassistant.components.http.auth import async_sign_path
from homeassistant.helpers.network import NoURLAvailableError


def wav_audio():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(16000)
        audio.writeframes(bytes(range(256)) * 125)
    return output.getvalue()


async def test_custom_chime_recording_and_live_encoding(tmp_path):
    ffmpeg = shutil.which("ffmpeg")
    path = tmp_path / "chime.wav"
    path.write_bytes(wav_audio())
    encoded, content_type = await core.to_playable(
        ffmpeg, wav_audio(), "audio/wav", 0, chime_url=str(path)
    )
    assert content_type == "audio/mpeg" and core.mp3_seconds(encoded) > 1.5
    with pytest.raises(core.TranscodeError):
        await core.to_playable(ffmpeg, b"invalid", "audio/webm", 0)
    with pytest.raises(core.TranscodeError, match="Unknown chime"):
        await core.render_chime(ffmpeg, "missing")
    for chime in (str(path), None):
        stream = LiveStream(ffmpeg, chime_url=chime)
        stream.feed(b"ignored before start")
        await stream.start()
        stream.feed(bytes(range(256)) * 250)
        stream.end()
        await stream.wait_done(5)
        assert stream.done and stream.mp3


async def test_live_abort_cap_and_expiry():
    stream = LiveStream(shutil.which("ffmpeg"))
    await stream.start()
    stream.pcm = bytearray(MAX_SECONDS * 32000)
    stream.feed(b"extra")
    assert len(stream.pcm) == MAX_SECONDS * 32000
    await stream.abort()
    assert stream.done
    store = LiveStore()
    store.add(stream)
    stream.created -= 241
    assert store.get(stream.id) is None


async def test_http_rejects_invalid_empty_and_oversized_uploads(
    hass, setup_roomcall, hass_client, hass_client_no_auth
):
    client = await hass_client()
    assert (await client.post("/api/roomcall/talk?priority=weird", data=b"audio")).status == 400
    assert (await client.post("/api/roomcall/talk", data=b"")).status == 400
    with patch("custom_components.roomcall.http.MAX_UPLOAD_BYTES", 8):
        assert (await client.post("/api/roomcall/talk", data=b"x" * 9)).status == 413
    assert (await client.post("/api/roomcall/command", data=b"x" * 65537)).status == 400
    speaker = await hass_client_no_auth()
    assert (await speaker.get("/api/roomcall/clip/missing.mp3")).status == 401
    assert (await client.get("/api/roomcall/clip/missing.mp3")).status == 404
    assert (await client.get("/api/roomcall/history/missing.mp3")).status == 404
    assert (await speaker.head("/api/roomcall/live/missing.mp3")).status == 401
    assert (await client.head("/api/roomcall/live/missing.mp3")).status == 404
    stream = LiveStream("ffmpeg")
    hass.data["roomcall"].live.add(stream)
    path = f"/api/roomcall/live/{stream.id}.mp3"
    assert (await speaker.head(path)).status == 401
    signed = async_sign_path(hass, path, timedelta(seconds=60), use_content_user=True)
    assert (await speaker.head(signed)).status == 200
    stream.done = True


async def test_websocket_routes_before_setup(hass, setup_roomcall, hass_ws_client):
    client = await hass_ws_client(hass)
    data = hass.data.pop("roomcall")
    try:
        for index, message in enumerate(
            (
                {"type": "roomcall/live"},
                {
                    "type": "roomcall/station",
                    "station_id": "station-a",
                    "room": "kitchen",
                    "name": "A",
                },
                {
                    "type": "roomcall/session",
                    "station_id": "station-a",
                    "action": "call",
                    "peer": "station-b",
                },
            ),
            1,
        ):
            await client.send_json({"id": index, **message})
            result = await client.receive_json()
            assert result["error"]["code"] == "not_ready"
    finally:
        hass.data["roomcall"] = data


async def test_websocket_invalid_stations_and_missing_url(hass, setup_roomcall, hass_ws_client):
    client = await hass_ws_client(hass)
    await client.send_json(
        {
            "id": 1,
            "type": "roomcall/station",
            "station_id": "station-a",
            "room": "missing",
            "name": "A",
        }
    )
    assert (await client.receive_json())["error"]["code"] == "no_targets"
    await client.send_json(
        {
            "id": 2,
            "type": "roomcall/session",
            "station_id": "station-a",
            "action": "call",
            "peer": "station-b",
        }
    )
    assert (await client.receive_json())["error"]["code"] == "station_unavailable"
    with patch("custom_components.roomcall.http.base_url", side_effect=NoURLAvailableError):
        await client.send_json({"id": 3, "type": "roomcall/live"})
        assert (await client.receive_json())["error"]["code"] == "no_url"


@pytest.mark.parametrize("error", [CallError("refused"), RuntimeError("provider failed")])
async def test_websocket_audio_failure_cleans_up(hass, setup_roomcall, hass_ws_client, error):
    client = await hass_ws_client(hass)
    with patch.object(ws, "place_call", AsyncMock(side_effect=error)):
        await client.send_json({"id": 1, "type": "roomcall/live"})
        messages = [await client.receive_json() for _ in range(3)]
        assert any(message.get("event", {}).get("type") == "error" for message in messages)
    await hass.async_block_till_done(wait_background_tasks=True)
    for stream in hass.data["roomcall"].live._streams.values():
        await stream.wait_done(5)
    assert all(stream.done for stream in hass.data["roomcall"].live._streams.values())


async def test_cancelled_transcode_reaps_child():
    proc = Mock(returncode=None)
    proc.communicate = AsyncMock(side_effect=[asyncio.CancelledError(), (b"", b"")])
    with (
        patch.object(core.asyncio, "create_subprocess_exec", AsyncMock(return_value=proc)),
        pytest.raises(asyncio.CancelledError),
    ):
        await core.to_playable("ffmpeg", b"speech", "audio/webm", 0)
    proc.kill.assert_called_once()
    assert proc.communicate.await_count == 2


async def test_tempo_changes_voice_length():
    ffmpeg = shutil.which("ffmpeg")
    normal, _ = await core.to_playable(ffmpeg, wav_audio(), "audio/wav", 0, tempo=1.0)
    slower, content_type = await core.to_playable(ffmpeg, wav_audio(), "audio/wav", 0, tempo=0.5)
    # 1.0 with no other processing passes the WAV through untouched.
    assert normal == wav_audio()
    assert content_type == "audio/mpeg"
    assert 1.7 < core.mp3_seconds(slower) < 2.4
