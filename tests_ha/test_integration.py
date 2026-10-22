"""End-to-end tests for the RoomCall Home Assistant integration."""

from __future__ import annotations

import asyncio
import io
import shutil
import subprocess
import tempfile
import wave
from pathlib import Path

import pytest
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import async_mock_service

DOMAIN = "roomcall"


def _wav() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * 800)
    return buf.getvalue()


async def test_config_flow_creates_entry(hass: HomeAssistant, house) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"speakers": ["media_player.kitchen_sonos"], "gain_db": 0}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["speakers"] == ["media_player.kitchen_sonos"]


async def test_config_flow_requires_speakers(hass: HomeAssistant, house) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"speakers": [], "gain_db": 0}
    )
    assert result["errors"] == {"speakers": "no_speakers"}


async def test_broadcast_message_to_one_room(hass: HomeAssistant, setup_roomcall, house) -> None:
    calls = async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("tts.piper", "unknown")
    response = await hass.services.async_call(
        DOMAIN,
        "broadcast",
        {"areas": [house["kitchen"]], "message": "Dinner is ready"},
        blocking=True,
        return_response=True,
    )
    played = sorted(c.data["entity_id"] for c in calls)
    assert played == ["media_player.kitchen_hub", "media_player.kitchen_sonos"]
    assert all(c.data["announce"] is True for c in calls)
    assert calls[0].data["media_content_id"].startswith("http://ha.local:8123/api/roomcall/clip/")
    assert calls[0].data["extra"] == {"volume": 40}
    assert response["ok"] is True
    state = hass.states.get("sensor.roomcall_last_call")
    assert state is not None and state.attributes["kind"] == "message"


async def test_broadcast_by_area_name_and_everywhere(hass: HomeAssistant, setup_roomcall) -> None:
    calls = async_mock_service(hass, "media_player", "play_media")
    await hass.services.async_call(
        DOMAIN, "broadcast", {"areas": ["Basement"], "media_url": "http://x/a.mp3"}, blocking=True
    )
    assert [c.data["entity_id"] for c in calls] == ["media_player.basement_sonos"]
    calls.clear()
    await hass.services.async_call(
        DOMAIN, "broadcast", {"media_url": "http://x/a.mp3"}, blocking=True
    )
    assert len(calls) == 3


async def test_rooms_view(hass: HomeAssistant, setup_roomcall, hass_client) -> None:
    client = await hass_client()
    body = await (await client.get("/api/roomcall/rooms")).json()
    assert body["speakers"] == 3
    names = {r["name"]: len(r["speakers"]) for r in body["rooms"]}
    assert names == {"Basement": 1, "Kitchen": 2}


async def test_talk_uploads_plays_and_serves_clip(
    hass: HomeAssistant, setup_roomcall, house, hass_client, hass_client_no_auth
) -> None:
    calls = async_mock_service(hass, "media_player", "play_media")
    client = await hass_client()
    resp = await client.post(
        f"/api/roomcall/talk?target={house['basement']}",
        data=_wav(),
        headers={"Content-Type": "audio/wav"},
    )
    body = await resp.json()
    assert resp.status == 200, body
    assert [r["entity_id"] for r in body["results"]] == ["media_player.basement_sonos"]
    url = calls[0].data["media_content_id"]
    assert url.startswith("http://ha.local:8123/api/roomcall/clip/")
    anon = await hass_client_no_auth()
    clip = await anon.get(url.removeprefix("http://ha.local:8123"))
    assert clip.status == 200
    audio = await clip.read()
    # Chime is on by default, so the WAV is re-encoded to MP3 with the chirp prepended.
    assert audio[:3] == b"ID3" or audio[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2")
    assert body["seconds"] and body["seconds"] > 0.3


def _iphone_mp4() -> bytes:
    """AAC in MP4 with the index (moov atom) at the end, as Safari's MediaRecorder writes it."""
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg not installed")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "rec.mp4"
        subprocess.run(
            [
                ffmpeg,
                "-hide_banner",
                "-loglevel",
                "error",
                "-f",
                "lavfi",
                "-t",
                "1",
                "-i",
                "sine=frequency=440:sample_rate=44100",
                "-c:a",
                "aac",
                str(out),
            ],
            check=True,
        )
        data = out.read_bytes()
    assert data.rfind(b"moov") > data.find(b"mdat")  # index after the audio
    return data


async def test_talk_accepts_iphone_mp4(
    hass: HomeAssistant, setup_roomcall, house, hass_client
) -> None:
    async_mock_service(hass, "media_player", "play_media")
    client = await hass_client()
    data = _iphone_mp4()

    async def chunks():  # a reverse proxy forwards the upload in pieces
        for i in range(0, len(data), 1024):
            yield data[i : i + 1024]
            await asyncio.sleep(0)

    resp = await client.post(
        f"/api/roomcall/talk?target={house['basement']}",
        data=chunks(),
        headers={"Content-Type": "audio/mp4"},
    )
    body = await resp.json()
    assert resp.status == 200, body
    assert body["seconds"] > 0.8


async def test_talk_requires_auth(hass: HomeAssistant, setup_roomcall, hass_client_no_auth) -> None:
    anon = await hass_client_no_auth()
    resp = await anon.post("/api/roomcall/talk?target=all", data=_wav())
    assert resp.status == 401


async def test_unknown_room_is_404(hass: HomeAssistant, setup_roomcall, hass_client) -> None:
    client = await hass_client()
    resp = await client.post("/api/roomcall/talk?target=attic", data=_wav())
    assert resp.status == 404


async def test_unload(hass: HomeAssistant, setup_roomcall) -> None:
    assert await hass.config_entries.async_unload(setup_roomcall.entry_id)
    assert DOMAIN not in hass.data


async def test_message_has_builtin_chime_even_with_chime_tts_loaded(
    hass: HomeAssistant, setup_roomcall, house
) -> None:
    hass.config.components.add("chime_tts")
    chime_calls = async_mock_service(hass, "chime_tts", "say")
    play_calls = async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("tts.piper", "unknown")
    await hass.services.async_call(
        DOMAIN, "broadcast", {"areas": [house["basement"]], "message": "Hi"}, blocking=True
    )
    assert not chime_calls
    assert (
        play_calls[0].data["media_content_id"].startswith("http://ha.local:8123/api/roomcall/clip/")
    )
    assert play_calls[0].data["announce"] is True
    assert play_calls[0].data["extra"] == {"volume": 40}


async def test_chime_can_be_disabled(hass: HomeAssistant, house) -> None:
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN, data={"speakers": ["media_player.basement_sonos"], "chime": False}
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    hass.config.components.add("chime_tts")
    hass.states.async_set("tts.piper", "unknown")
    play_calls = async_mock_service(hass, "media_player", "play_media")
    await hass.services.async_call(DOMAIN, "broadcast", {"message": "Hi"}, blocking=True)
    assert play_calls[0].data["media_content_id"].startswith("media-source://tts/")
