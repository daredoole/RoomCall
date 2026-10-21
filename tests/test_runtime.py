"""Exercise CLI, network adapters and failure handling without real LAN traffic."""

from __future__ import annotations

import runpy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from roomcall import cli, config, demo, discovery
from roomcall.config import Config
from roomcall.models import SpeakerKind
from roomcall.sinks import SinkUnavailable, default_sinks
from roomcall.transcode import TranscodeError, to_playable


def test_config_defaults_yaml_and_lan_address(tmp_path, monkeypatch):
    assert Config.load(None) == Config.load(tmp_path / "absent")
    path = tmp_path / "roomcall.yaml"
    path.write_text(
        "volume: 0.4\nrooms: {Kitchen: [Hub]}\nspeakers: [{kind: cast, host: 10.0.0.2}]\n"
    )
    loaded = Config.load(path)
    assert loaded.volume == 0.4 and loaded.static_speakers[0].name == "10.0.0.2"
    path.write_text("")
    assert Config.load(path) == Config()
    sock = MagicMock()
    sock.__enter__.return_value = sock
    sock.getsockname.return_value = ("10.0.0.8", 123)
    monkeypatch.setattr(config.socket, "socket", Mock(return_value=sock))
    assert Config().base_url() == "http://10.0.0.8:8765"
    sock.connect.side_effect = OSError
    assert config.lan_ip() == "127.0.0.1"


def test_cli_discover_and_serve(monkeypatch, speakers, capsys, tmp_path):
    monkeypatch.setattr(cli, "discover", AsyncMock(return_value=[]))
    assert cli.main(["discover", "--timeout", "0"]) == 1
    monkeypatch.setattr(cli, "discover", AsyncMock(return_value=speakers))
    assert cli.main(["-v", "discover"]) == 0
    assert "Basement" in capsys.readouterr().out
    run = Mock()
    monkeypatch.setattr(cli.web, "run_app", run)
    assert cli.main(["serve", "--config", str(tmp_path / "absent"), "--port", "9988"]) == 0
    assert run.call_args.kwargs["port"] == 9988
    assert cli.main(["serve", "--demo"]) == 0
    from roomcall.server import STATE_KEY

    assert run.call_args.args[0][STATE_KEY].config.rooms == demo.DEMO_ROOMS


async def test_cli_send_auth_and_exit_status(aiohttp_server, tmp_path, monkeypatch, capsys):
    from aiohttp import web

    seen = []

    async def receive(request):
        seen.append((request.query["target"], request.headers, await request.read()))
        return web.Response(text="accepted", status=200 if len(seen) == 1 else 207)

    app = web.Application()
    app.router.add_post("/api/talk", receive)
    server = await aiohttp_server(app)
    url = str(server.make_url("/"))
    assert await cli._send(url, "Kitchen", "clip.mp3", b"audio", "secret") == 0
    assert seen[0][1]["X-RoomCall-Token"] == "secret"
    assert await cli._send(url, "all", "clip.unknown", b"x", None) == 1
    assert seen[1][1]["Content-Type"] == "application/octet-stream"
    assert "accepted" in capsys.readouterr().out


def test_cli_file_command_and_module_entry(monkeypatch, tmp_path):
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"audio")
    send = AsyncMock(return_value=0)
    monkeypatch.setattr(cli, "_send", send)
    assert cli.main(["send", "all", str(audio), "--token", "secret"]) == 0
    send.assert_awaited_once_with("http://127.0.0.1:8765", "all", "clip.wav", b"audio", "secret")
    monkeypatch.setattr(sys, "argv", ["roomcall", "--version"])
    monkeypatch.delitem(sys.modules, "roomcall.cli")
    with pytest.raises(SystemExit) as exc:
        runpy.run_module("roomcall.cli", run_name="__main__")
    assert exc.value.code == 0


async def test_demo_backend(monkeypatch):
    sleep = AsyncMock()
    monkeypatch.setattr(demo.asyncio, "sleep", sleep)
    assert await demo.demo_discover(0) == demo.DEMO_SPEAKERS
    await demo.DemoSink().play(demo.DEMO_SPEAKERS[0], "http://clip", None)
    sleep.assert_awaited_once()


async def test_mdns_browse_resolves_and_closes(monkeypatch):
    import zeroconf
    import zeroconf.asyncio as zasync

    azc = SimpleNamespace(zeroconf=object(), async_close=AsyncMock())
    browser = SimpleNamespace(async_cancel=AsyncMock())
    info = Mock(port=8009, properties={b"fn": b"Hub", b"none": None})
    info.parsed_addresses.return_value = ["invalid", "10.0.0.1"]
    info.async_request = AsyncMock(side_effect=[True, False, True])
    monkeypatch.setattr(zasync, "AsyncZeroconf", Mock(return_value=azc))
    monkeypatch.setattr(zasync, "AsyncServiceInfo", Mock(return_value=info))

    def browse(zc, types, handlers):
        handler = handlers[0]
        for kind in ("_googlecast._tcp.local.", "_sonos._tcp.local.", "_http._tcp.local."):
            handler(zc, kind, "Hub", zeroconf.ServiceStateChange.Added)
        handler(zc, types[0], "removed", zeroconf.ServiceStateChange.Removed)
        return browser

    monkeypatch.setattr(zasync, "AsyncServiceBrowser", browse)
    found = await discovery.discover(0)
    assert len(found) == 1 and found[0].name == "Hub"
    browser.async_cancel.assert_awaited_once()
    azc.async_close.assert_awaited_once()


@pytest.mark.parametrize(
    "kind,module", [("sonos", "soco"), ("airplay", "pyatv"), ("cast", "pychromecast")]
)
async def test_optional_backend_missing(kind, module, monkeypatch, speakers):
    monkeypatch.setitem(sys.modules, module, None)
    with pytest.raises(SinkUnavailable, match=f"roomcall\\[{kind}\\]"):
        await default_sinks()[SpeakerKind(kind)].play(speakers[0], "http://clip", None)


async def test_airplay_scan_volume_stream_and_close(monkeypatch, speakers):
    atv = SimpleNamespace(
        audio=SimpleNamespace(set_volume=AsyncMock()),
        stream=SimpleNamespace(stream_file=AsyncMock()),
        close=Mock(),
    )
    api = SimpleNamespace(scan=AsyncMock(return_value=[]), connect=AsyncMock(return_value=atv))
    monkeypatch.setitem(sys.modules, "pyatv", api)
    sink = default_sinks()[SpeakerKind.AIRPLAY]
    with pytest.raises(ConnectionError, match="did not answer"):
        await sink.play(speakers[2], "http://clip", None)
    api.scan.return_value = [object()]
    await sink.play(speakers[2], "http://clip", 0.4)
    atv.audio.set_volume.assert_awaited_once_with(40)
    atv.stream.stream_file.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError):
        await sink.play(speakers[2], "http://clip", None)
    assert atv.close.call_count == 2


async def test_cast_play_and_disconnect_on_error(monkeypatch, speakers):
    cast = Mock()
    monkeypatch.setitem(
        sys.modules,
        "pychromecast",
        SimpleNamespace(get_chromecast_from_host=Mock(return_value=cast)),
    )
    sink = default_sinks()[SpeakerKind.CAST]
    await sink.play(speakers[3], "http://clip", 0.25)
    cast.set_volume.assert_called_once_with(0.25)
    cast.media_controller.play_media.assert_called_once_with(
        "http://clip", "audio/mpeg", title="RoomCall"
    )
    cast.media_controller.play_media.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError):
        await sink.play(speakers[3], "http://clip", None)
    assert cast.disconnect.call_count == 2


async def test_sonos_snapshot_play_and_restore(monkeypatch, speakers):
    from roomcall.sinks import sonos

    device, snapshot = Mock(), Mock()
    device.get_current_transport_info.side_effect = [
        {"current_transport_state": "PLAYING"},
        {"current_transport_state": "STOPPED"},
    ]
    monkeypatch.setitem(sys.modules, "soco", SimpleNamespace(SoCo=Mock(return_value=device)))
    monkeypatch.setitem(
        sys.modules, "soco.snapshot", SimpleNamespace(Snapshot=Mock(return_value=snapshot))
    )
    monkeypatch.setattr(sonos.time, "sleep", Mock())
    sink = default_sinks()[SpeakerKind.SONOS]
    await sink.play(speakers[0], "http://clip", 0.35)
    assert device.volume == 35
    snapshot.restore.assert_called_once_with(fade=True)
    device.play_uri.side_effect = RuntimeError("offline")
    with pytest.raises(RuntimeError):
        await sink.play(speakers[0], "http://clip", None)
    assert snapshot.restore.call_count == 2


async def test_ffmpeg_real_conversion_and_failures(monkeypatch):
    from roomcall import transcode
    from tests.test_server import _wav

    audio, ext = await to_playable(_wav(), "audio/wav", gain_db=-3)
    assert ext == "mp3" and len(audio) > 100
    with pytest.raises(TranscodeError):
        await to_playable(b"invalid", "audio/webm")
    monkeypatch.setattr(transcode.shutil, "which", lambda name: None)
    with pytest.raises(TranscodeError, match="ffmpeg is required"):
        await to_playable(b"x", "audio/webm")
