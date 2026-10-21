"""HomeKit stream negotiation, startup failures and resource ownership."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from custom_components.roomcall import _start_homekit, homekit
from custom_components.roomcall.calls import CallError
from custom_components.roomcall.diagnostics import async_get_config_entry_diagnostics


def camera(intercom):
    from pyhap.accessory_driver import AccessoryDriver
    from pyhap.loader import get_loader

    driver = MagicMock(spec=AccessoryDriver)
    driver.loader = get_loader()
    result = homekit._camera_class()(
        homekit.camera_options("127.0.0.1"), driver, "Kitchen Intercom"
    )
    result.setup_intercom(
        intercom, "kitchen", "/tmp/kitchen.png", homekit.render_room_picture("Kitchen", (320, 180))
    )
    return result


async def test_camera_negotiation_stream_callbacks_and_stop(hass, setup_roomcall):
    intercom = homekit.HomeKitIntercom(hass, hass.data["roomcall"], 51800)
    item = camera(intercom)
    configuration = {
        "address": "127.0.0.1",
        "v_port": 51000,
        "v_srtp_key": "video",
        "v_ssrc": 12345,
        "a_port": 51002,
        "a_srtp_key": "audio",
    }
    video = SimpleNamespace(
        stderr=SimpleNamespace(readline=AsyncMock(side_effect=[b"warning\n", b" \n", b""])),
        returncode=None,
        terminate=Mock(),
        wait=AsyncMock(),
        kill=Mock(),
    )

    async def receive(ffmpeg, sdp, phrase, session, label, *, on_start, on_audio, on_end):
        assert "51002" in sdp
        on_start(b"first")
        on_audio(b"next")
        on_end()
        phrase(b"firstnext")

    session = {}
    with (
        patch.object(homekit.asyncio, "create_subprocess_exec", AsyncMock(return_value=video)),
        patch.object(homekit, "talk_loop", receive),
        patch.object(homekit, "_LivePhrase") as phrase,
    ):
        assert await item.start_stream(session, configuration)
        await session["loop"]
        await session["video_log"]
        phrase.return_value.feed.assert_called_once_with(b"next")
        phrase.return_value.end.assert_called_once()
        assert await item.reconfigure_stream(session, configuration)
        assert (await item.async_get_snapshot({})).startswith(b"\xff\xd8")
        await item.stop_stream(session)
    assert not session["active"]
    video.terminate.assert_called_once()
    with patch.object(
        homekit.asyncio, "create_subprocess_exec", AsyncMock(side_effect=OSError("unavailable"))
    ):
        assert not await item.start_stream({}, configuration)


async def test_process_termination_timeout_and_completed_process():
    proc = SimpleNamespace(
        returncode=0, terminate=Mock(), wait=AsyncMock(side_effect=TimeoutError), kill=Mock()
    )
    await homekit._terminate(proc)
    proc.terminate.assert_not_called()
    proc.returncode = None
    await homekit._terminate(proc)
    proc.kill.assert_called_once()


def test_camera_numeric_values_and_older_pillow():
    from PIL import ImageFont

    assert homekit._byte(None, 110) == 110
    assert homekit._byte(96, 110) == 96
    original = ImageFont.load_default

    def older_default(*args, **kwargs):
        if kwargs:
            raise TypeError("older Pillow")
        return original()

    with patch.object(ImageFont, "load_default", older_default):
        assert homekit.render_room_picture("Kitchen", (320, 180)).startswith(b"\x89PNG")


@pytest.mark.parametrize("error", [CallError("muted"), RuntimeError("offline")])
async def test_phrase_refused_before_stream_opens(hass, error):
    intercom = SimpleNamespace(hass=hass, async_live_start=AsyncMock(side_effect=error))
    phrase = homekit._LivePhrase(intercom, "kitchen", b"first")
    phrase.feed(b"next")
    phrase.end()
    await hass.async_block_till_done()
    assert phrase._stream is None


async def test_phrase_buffers_audio_and_early_end(hass):
    stream = Mock()
    ready = asyncio.Event()

    async def start(area):
        await ready.wait()
        return stream

    intercom = SimpleNamespace(hass=hass, async_live_start=start)
    phrase = homekit._LivePhrase(intercom, "kitchen", b"first")
    phrase.feed(b"next")
    phrase.end()
    ready.set()
    await hass.async_block_till_done()
    stream.feed.assert_called_once_with(b"firstnext")
    stream.end.assert_called_once()


async def test_bridge_start_and_delivery_failures_stop_resources(hass, setup_roomcall):
    data = hass.data["roomcall"]
    with (
        patch.object(
            homekit.HomeKitIntercom, "async_start", AsyncMock(side_effect=OSError("port busy"))
        ),
        patch.object(homekit.HomeKitIntercom, "async_stop", AsyncMock()) as stop,
    ):
        await _start_homekit(hass, data)
        stop.assert_awaited_once()
        assert data.homekit is None
    intercom = homekit.HomeKitIntercom(hass, data, 51800)
    with patch(
        "custom_components.roomcall.calls.place_call",
        AsyncMock(side_effect=RuntimeError("provider")),
    ):
        stream = await intercom.async_live_start("kitchen")
        await hass.async_block_till_done(wait_background_tasks=True)
        assert stream.done


async def test_diagnostics_report_bridge_ports_and_pairing(hass, setup_roomcall):
    data = hass.data["roomcall"]
    data.homekit = SimpleNamespace(
        drivers={"kitchen": SimpleNamespace(state=SimpleNamespace(paired=True))}, port=51800
    )
    try:
        result = await async_get_config_entry_diagnostics(hass, setup_roomcall)
        assert result["homekit"] == {
            "rooms": ["kitchen"],
            "paired": {"kitchen": True},
            "first_port": 51800,
        }
    finally:
        data.homekit = None
