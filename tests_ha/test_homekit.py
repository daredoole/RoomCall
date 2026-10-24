"""Apple Home intercom: talk-back decoding, camera accessories and the options toggle."""

from __future__ import annotations

import asyncio
import base64
import os
import random
import shutil
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from custom_components.roomcall.homekit import (
    HomeKitIntercom,
    _camera_class,
    camera_options,
    render_room_picture,
    state_file,
    talk_loop,
    talk_sdp,
    video_command,
)
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

DOMAIN = "roomcall"
FFMPEG = shutil.which("ffmpeg")


def _has_libopus() -> bool:
    if FFMPEG is None:
        return False
    out = subprocess.run([FFMPEG, "-hide_banner", "-encoders"], capture_output=True, text=True)
    return "libopus" in out.stdout


needs_opus = pytest.mark.skipif(not _has_libopus(), reason="ffmpeg with libopus needed")


def _even_udp_port() -> int:
    # The test harness blocks sockets, so pick a likely-free port instead of probing.
    return random.randrange(40000, 60000, 2)


async def _iphone_talks(port: int, key: str, voice: str) -> None:
    """Send Opus over SRTP the way the Home app's talk button does."""
    src = f"aevalsrc='if({voice},0.4*sin(2*PI*300*t)*sin(2*PI*3*t),0.002*random(0))':s=48000:d=3"
    proc = await asyncio.create_subprocess_exec(
        FFMPEG, "-hide_banner", "-loglevel", "error", "-re", "-f", "lavfi", "-i", src,
        "-c:a", "libopus", "-ac", "1", "-ar", "48000", "-b:a", "24k", "-application", "voip",
        "-payload_type", "110", "-ssrc", "4242", "-f", "rtp",
        "-srtp_out_suite", "AES_CM_128_HMAC_SHA1_80", "-srtp_out_params", key,
        f"srtp://127.0.0.1:{port}?rtcpport={port}&pkt_size=1378",
    )  # fmt: skip
    await proc.wait()


@needs_opus
async def test_talk_back_is_decoded_and_split_into_phrases():
    port = _even_udp_port()
    key = base64.b64encode(os.urandom(30)).decode()
    phrases: list[bytes] = []
    session = {"active": True}
    loop = asyncio.create_task(
        talk_loop(FFMPEG, talk_sdp("127.0.0.1", port, key), phrases.append, session)
    )
    await asyncio.sleep(0.8)
    await _iphone_talks(port, key, "between(t,0.4,1.6)")
    # The phrase must play as soon as the iPhone stops sending, not when the camera closes.
    await asyncio.sleep(1.0)
    assert len(phrases) == 1
    # The receiver exits when the talk button goes quiet; RoomCall must start a new one.
    await asyncio.sleep(1.2)
    first = session["talk"]
    first.kill()  # stands in for ffmpeg's own exit after 20 s without packets
    await asyncio.sleep(1.0)
    assert session["talk"] is not first  # a new receiver took over
    await _iphone_talks(port, key, "between(t,0.4,1.4)")
    await asyncio.sleep(1.2)
    session["active"] = False
    session["talk"].kill()
    await asyncio.wait_for(loop, 10)
    assert len(phrases) == 2
    assert all(len(p) / 32000 > 0.9 for p in phrases)


def test_talk_sdp():
    sdp = talk_sdp("192.168.1.20", 50000, "KEY==")
    assert "m=audio 50000 RTP/AVP 110" in sdp
    assert "a=rtpmap:110 opus/48000/2" in sdp
    assert "a=crypto:1 AES_CM_128_HMAC_SHA1_80 inline:KEY==" in sdp
    assert "c=IN IP6 fe80::1" in talk_sdp("fe80::1", 1, "k", ipv6=True)


def test_video_command_uses_the_negotiated_stream():
    cmd = video_command(
        "ffmpeg",
        "/tmp/room.png",
        {
            "address": "192.168.1.20",
            "v_port": 51000,
            "v_srtp_key": "VKEY",
            "v_ssrc": 12345,
            "v_payload_type": b"\x63",
            "v_max_bitrate": 299,
            "v_max_mtu": (1200).to_bytes(2, "little"),
            "width": 640,
            "height": 360,
            "fps": 15,
        },
    )
    joined = " ".join(cmd)
    assert "-payload_type 99" in joined
    assert "-ssrc 12345" in joined
    assert "-srtp_out_params VKEY" in joined
    assert "scale=640:360" in joined
    assert cmd[-1] == "srtp://192.168.1.20:51000?rtcpport=51000&localrtcpport=51000&pkt_size=1200"


def test_room_picture_is_a_png():
    png = render_room_picture("Basement", (320, 180))
    assert png[:8] == b"\x89PNG\r\n\x1a\n"


def test_state_files_are_per_room_and_safe():
    assert state_file("basement") == state_file("basement")
    assert state_file("basement") != state_file("kitchen")
    assert "/" not in state_file("../evil/room")


def test_room_camera_has_a_talk_button():
    from pyhap.accessory_driver import AccessoryDriver
    from pyhap.loader import get_loader

    driver = MagicMock(spec=AccessoryDriver)
    driver.loader = get_loader()
    camera = _camera_class()(
        camera_options("192.168.1.20"),
        driver,
        "Basement Intercom",
    )
    camera.setup_intercom(MagicMock(), "basement", "/tmp/x.png", render_room_picture("Basement"))
    services = {s.display_name for s in camera.services}
    assert {"CameraRTPStreamManagement", "Microphone", "Speaker"} <= services
    assert camera.get_snapshot({})[:2] == b"\xff\xd8"  # JPEG


async def test_apple_home_option_starts_and_stops_the_bridge(hass: HomeAssistant, house):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"speakers": ["media_player.basement_sonos"]},
        options={"speakers": ["media_player.basement_sonos"], "homekit": {"enabled": True}},
    )
    entry.add_to_hass(hass)

    seen_rooms: list[list[dict]] = []

    async def start(self, rooms):
        seen_rooms.append(rooms)
        self.pincode = "123-45-678"

    stopped = AsyncMock()
    notify = MagicMock()
    with (
        patch("custom_components.roomcall.persistent_notification.async_create", notify),
        patch.object(HomeKitIntercom, "async_start", start),
        patch.object(HomeKitIntercom, "async_stop", stopped),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert [r["name"] for r in seen_rooms[0]] == ["Basement"]
        assert "123-45-678" in notify.call_args.args[1]
        assert await hass.config_entries.async_unload(entry.entry_id)
        stopped.assert_awaited()


async def test_apple_home_is_off_by_default(hass: HomeAssistant, setup_roomcall):
    assert hass.data[DOMAIN].homekit is None


@needs_opus
async def test_phrase_ends_when_the_talk_button_is_released():
    """The iPhone stops sending instead of sending silence; the phrase must still end fast."""
    import time

    port = _even_udp_port()
    key = base64.b64encode(os.urandom(30)).decode()
    got: list[float] = []
    session = {"active": True}
    loop = asyncio.create_task(
        talk_loop(
            FFMPEG,
            talk_sdp("127.0.0.1", port, key),
            lambda _p: got.append(time.monotonic()),
            session,
        )
    )
    await asyncio.sleep(0.8)
    await _iphone_talks(port, key, "1")  # voice right up to the release
    released = time.monotonic()
    await asyncio.sleep(2.0)
    session["active"] = False
    session["talk"].kill()
    await asyncio.wait_for(loop, 10)
    assert len(got) == 1
    assert got[0] - released < 1.5


@needs_opus
async def test_live_call_starts_while_still_talking():
    import time

    port = _even_udp_port()
    key = base64.b64encode(os.urandom(30)).decode()
    events: list[tuple[str, float]] = []
    session = {"active": True}
    loop = asyncio.create_task(
        talk_loop(
            FFMPEG,
            talk_sdp("127.0.0.1", port, key),
            lambda _p: None,
            session,
            on_start=lambda _p: events.append(("start", time.monotonic())),
            on_audio=lambda _p: events.append(("audio", time.monotonic())),
            on_end=lambda: events.append(("end", time.monotonic())),
        )
    )
    await asyncio.sleep(0.8)
    await _iphone_talks(port, key, "1")  # 3 s of talking
    released = time.monotonic()
    await asyncio.sleep(1.5)
    session["active"] = False
    session["talk"].kill()
    await asyncio.wait_for(loop, 10)
    started = next(t for k, t in events if k == "start")
    assert released - started > 2.0  # the call began long before the caller let go
    assert any(k == "audio" for k, _ in events)
    assert events[-1][0] == "end"


async def test_bridge_publishes_one_camera_per_room_and_keeps_its_code(
    hass: HomeAssistant, setup_roomcall, caplog
):
    import tempfile
    import threading

    from custom_components.roomcall.core import rooms

    data = hass.data[DOMAIN]
    loop_thread = threading.current_thread()
    real_tmp = tempfile.TemporaryDirectory
    tmp_threads = []

    class TrackedTemporaryDirectory(real_tmp):
        """Records where the bridge's picture directory is created and removed."""

        def __init__(self, *args, **kwargs):
            tmp_threads.append(threading.current_thread())
            super().__init__(*args, **kwargs)

        def cleanup(self):
            tmp_threads.append(threading.current_thread())
            super().cleanup()

    with (
        patch(
            "custom_components.roomcall.homekit.tempfile.TemporaryDirectory",
            TrackedTemporaryDirectory,
        ),
        patch(
            "homeassistant.components.network.async_get_source_ip",
            AsyncMock(return_value="127.0.0.1"),
        ),
        patch(
            "homeassistant.components.zeroconf.async_get_async_instance",
            AsyncMock(return_value=MagicMock()),
        ),
        patch("pyhap.accessory_driver.AccessoryDriver.async_start", AsyncMock()),
        patch("pyhap.accessory_driver.AccessoryDriver.async_stop", AsyncMock()),
    ):
        first = HomeKitIntercom(hass, data, 51800)
        await first.async_start(rooms(hass, data.speakers))
        assert sorted(first.drivers) == sorted(r["area_id"] for r in rooms(hass, data.speakers))
        ports = sorted(d.state.port for d in first.drivers.values())
        assert ports == list(range(51800, 51800 + len(ports)))
        camera = next(iter(first.drivers.values())).accessory
        assert camera.display_name.endswith("Intercom")
        code = first.pincode
        # Anyone reading the log could pair a room; the code is only in the admin notification.
        # (The test harness's own mock storage logs what it writes; HA's real Store does not.)
        logged = [
            r.getMessage()
            for r in caplog.records
            if not r.name.startswith("pytest_homeassistant_custom_component")
        ]
        assert logged and not any(code in message for message in logged)
        await first.async_stop()
        # Creating and removing the directory is blocking I/O, done off the event loop.
        assert len(tmp_threads) == 2 and loop_thread not in tmp_threads

        second = HomeKitIntercom(hass, data, 51800)
        await second.async_start(rooms(hass, data.speakers))
        assert second.pincode == code  # same code after a restart
        await second.async_stop()


@needs_opus
async def test_apple_home_live_call_reaches_the_room(hass: HomeAssistant, setup_roomcall, house):
    from custom_components.roomcall.homekit import _LivePhrase

    played = async_mock_service(hass, "media_player", "play_media")
    data = hass.data[DOMAIN]
    intercom = HomeKitIntercom(hass, data, 51900)
    tone = bytes(range(256)) * 125  # 1 s of non-silent 16-bit audio
    call = _LivePhrase(intercom, house["basement"], tone[:6400])
    await hass.async_block_till_done()
    call.feed(tone[6400:])
    call.end()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert [c.data["entity_id"] for c in played] == ["media_player.basement_sonos"]
    assert "/api/roomcall/live/" in played[0].data["media_content_id"]
    record = data.history.records()[0]
    assert record.from_room == "Apple Home"
    assert record.has_audio
