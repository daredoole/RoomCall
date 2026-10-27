"""Encrypted live audio, physical answer consent, ownership and teardown."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct
import subprocess
from unittest.mock import AsyncMock, patch

import pytest
from custom_components.roomcall.echo_transport import PROTOCOL, EchoLink
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.stations import Station
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from noise.connection import NoiseConnection
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

WORD = "roomcall-test-house-word-not-a-real-key"


@pytest.fixture
def pcm():
    """A real audio frame, produced by ffmpeg rather than assuming silence is sound."""
    return subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=830:sample_rate=16000",
            "-t",
            "0.02",
            "-f",
            "s16le",
            "-ac",
            "1",
            "pipe:1",
        ],
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout


async def read_record(reader):
    return await reader.readexactly(struct.unpack(">I", await reader.readexactly(4))[0])


async def write_record(writer, data):
    writer.write(struct.pack(">I", len(data)) + data)
    await writer.drain()


def responder():
    noise = NoiseConnection.from_name(b"Noise_NNpsk0_25519_ChaChaPoly_SHA256")
    noise.set_as_responder()
    noise.set_prologue(PROTOCOL)
    noise.set_psks(psk=hashlib.sha256(("techo5-intercom psk:" + WORD).encode()).digest())
    noise.start_handshake()
    return noise


async def test_noise_round_trip_and_fragmented_application_messages(pcm, socket_enabled):
    received = asyncio.Future()
    done = asyncio.Event()

    async def serve(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: techo5-intercom/1\r\n"
                b"Connection: Upgrade\r\n\r\n"
            )
            noise = responder()
            noise.read_message(await read_record(reader))
            await write_record(writer, bytes(noise.write_message()))
            hello = noise.decrypt(await read_record(reader))
            assert hello[0:1] == b"H" and hello[3:] == b"Phone"
            # The application message can cross Noise record boundaries.
            message = b"S" + struct.pack(">H", len(pcm)) + pcm
            await write_record(writer, noise.encrypt(message[:1]))
            await write_record(writer, noise.encrypt(message[1:]))
            received.set_result(noise.decrypt(await read_record(reader)))
            assert noise.decrypt(await read_record(reader)) == b"X\0\0"
        finally:
            writer.close()
            done.set()

    server = await asyncio.start_server(serve, "127.0.0.1", 0)
    async with server:
        link = await EchoLink.connect(
            "127.0.0.1", server.sockets[0].getsockname()[1], WORD, "Phone"
        )
        assert await link.receive() == (b"S", pcm)
        await link.send(b"S", pcm)
        assert await received == b"S" + struct.pack(">H", len(pcm)) + pcm
        await link.close()
        await asyncio.wait_for(done.wait(), 2)


@pytest.mark.parametrize("size", [0, 60065])
async def test_oversized_encrypted_records_rejected_before_allocation(size):
    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack(">I", size))
    link = EchoLink(reader, AsyncMock(), AsyncMock())
    with pytest.raises(ValueError, match="record size"):
        await link._read_record()


async def test_invalid_payload_and_send_bounds():
    link = EchoLink(AsyncMock(), AsyncMock(), AsyncMock())
    link.buffer.extend(b"S" + struct.pack(">H", 1281))
    with pytest.raises(ValueError, match="payload size"):
        await link.receive()
    for kind, payload in [(b"XX", b""), (b"S", bytes(1281))]:
        with pytest.raises(ValueError, match="message"):
            await link.send(kind, payload)


async def test_upgrade_failure_closes_socket():
    reader = asyncio.StreamReader()
    reader.feed_data(b"HTTP/1.1 404 Not Found\r\n\r\n")
    writer = AsyncMock()
    writer.write = lambda _: None
    # close() is synchronous, just as StreamWriter.close is.
    from unittest.mock import Mock

    writer.close = close = Mock()
    with (
        patch("asyncio.open_connection", AsyncMock(return_value=(reader, writer))),
        pytest.raises(ValueError, match="refused"),
    ):
        await EchoLink.connect("127.0.0.1", 8181, WORD, "Phone")
    close.assert_called_once()


@pytest.fixture
async def paired(hass, setup_roomcall):
    entry = MockConfigEntry(domain="esphome", data={})
    entry.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={("esphome", "echo")}, name="Echo"
    )
    registry = er.async_get(hass)
    speaker = registry.async_get_or_create(
        "media_player",
        "esphome",
        "echo-speaker",
        device_id=device.id,
        config_entry=entry,
        suggested_object_id="echo",
    ).entity_id
    answer = registry.async_get_or_create(
        "button",
        "esphome",
        "echo-answer",
        device_id=device.id,
        config_entry=entry,
        original_name="Answer call",
        suggested_object_id="echo_answer",
    ).entity_id
    hass.states.async_set(speaker, "idle")
    hass.states.async_set(answer, "unknown")
    data = hass.data["roomcall"]
    data.speakers.append(speaker)
    await data.echoes.pair(speaker, "192.168.1.176", WORD)
    return data.echoes, speaker, answer


class FakeLink:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.send = AsyncMock()
        self.close = AsyncMock()

    async def receive(self):
        value = await self.queue.get()
        if isinstance(value, Exception):
            raise value
        return value


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            changed = asyncio.Event()
            handle = asyncio.get_running_loop().call_later(0.001, changed.set)
            try:
                await changed.wait()
            finally:
                handle.cancel()


async def test_call_requires_answer_roundtrips_frames_and_releases_on_disconnect(hass, paired, pcm):
    manager, speaker, _ = paired
    events = []
    link = FakeLink()
    with patch("custom_components.roomcall.echo.EchoLink.connect", AsyncMock(return_value=link)):
        call_id = manager.start(speaker, "owner", "Phone", events.append)
        await until(lambda: manager.calls[call_id].link is not None)
        with pytest.raises(Problem, match="consent_required"):
            await manager.audio(call_id, "owner", base64.b64encode(pcm).decode())
        with pytest.raises(Problem, match="forbidden"):
            manager.owned(call_id, "someone_else")
        with pytest.raises(Problem, match="station_busy"):
            manager.start(speaker, "owner", "Other", events.append)
        await link.queue.put((b"R", b""))
        await link.queue.put((b"A", b""))
        await link.queue.put((b"S", pcm))
        await until(lambda: any(e["type"] == "audio" for e in events))
        assert base64.b64decode(events[-1]["pcm"]) == pcm
        await manager.audio(call_id, "owner", base64.b64encode(pcm * 2).decode())
        assert link.send.await_count == 2
        for bad in ("?", base64.b64encode(b"x").decode()):
            with pytest.raises(Problem, match="invalid_request"):
                await manager.audio(call_id, "owner", bad)
        manager.end(call_id, "disconnected")
        await until(lambda: link.close.await_count == 1)
        assert events[-1]["reason"] == "disconnected"
        with pytest.raises(Problem, match="not_found"):
            manager.owned(call_id, "owner")
    await manager.close()


@pytest.mark.parametrize(
    "message,reason",
    [
        (b"B", "station_busy"),
        (b"D", "declined"),
        (b"N", "declined"),
        (b"X", "ended"),
        (b"?", "station_unavailable"),
    ],
)
async def test_remote_control_and_protocol_failure(paired, message, reason):
    manager, speaker, _ = paired
    link, events = FakeLink(), []
    await link.queue.put((message, b""))
    with patch("custom_components.roomcall.echo.EchoLink.connect", AsyncMock(return_value=link)):
        call_id = manager.start(speaker, "owner", "Phone", events.append)
        await until(lambda: call_id not in manager.calls)
    assert events[-1]["reason"] == reason


@pytest.mark.parametrize(
    "error,reason",
    [(TimeoutError(), "timeout"), (OSError("private network detail"), "station_unavailable")],
)
async def test_connect_failure_is_bounded_and_redacted(paired, error, reason):
    manager, speaker, _ = paired
    events = []
    with patch("custom_components.roomcall.echo.EchoLink.connect", AsyncMock(side_effect=error)):
        call_id = manager.start(speaker, "owner", "Phone", events.append)
        await until(lambda: call_id not in manager.calls)
    assert events[-1]["reason"] == reason
    assert "private" not in str(events)


async def test_pairing_scope_secrets_and_persistence(hass, paired):
    manager, speaker, _ = paired
    assert WORD not in str(manager.list())
    assert manager.list()[0]["available"]
    hass.states.async_set(speaker, "unavailable")
    assert not manager.list()[0]["available"]
    for host, key in [("8.8.8.8", WORD), ("127.0.0.1", WORD), ("192.168.1.176", "short")]:
        with pytest.raises(Problem, match="invalid_request"):
            await manager.pair(speaker, host, key)
    for entity in ["media_player.missing", "media_player.kitchen_sonos"]:
        with pytest.raises(Problem, match="invalid_request"):
            await manager.pair(entity, "192.168.1.176", WORD)
    await manager.load()
    assert manager.peers[speaker]["word"] == WORD
    await manager.remove(speaker)
    assert not manager.list()
    with pytest.raises(Problem, match="station_unavailable"):
        manager.start(speaker, "owner", "Phone", list.append)


async def test_voice_invitation_requires_browser_answer_and_never_enables_dropin(hass, paired):
    manager, speaker, answer = paired
    events, call_events = [], []
    station = Station("browser", "owner", "kitchen", "Kitchen", events.append)
    invite = manager.invite(speaker, station)
    assert events[-1]["type"] == "echo_incoming"
    assert not manager.calls
    with pytest.raises(Problem, match="forbidden"):
        manager.start(speaker, "other", "Phone", call_events.append, invite)
    link = FakeLink()
    await link.queue.put((b"R", b""))
    with patch("custom_components.roomcall.echo.EchoLink.connect", AsyncMock(return_value=link)):
        service = async_mock_service(hass, "button", "press")
        manager.start(speaker, "owner", "Phone", call_events.append, invite)
        await until(lambda: bool(service))
        assert service[0].data == {"entity_id": answer}
        await manager.close()
    assert call_events[-1]["reason"] == "disconnected"


async def test_pairing_rejects_missing_or_ambiguous_answer_and_cleans_calls(hass, paired):
    manager, speaker, answer = paired
    registry = er.async_get(hass)
    registry.async_update_entity(answer, original_name="Other")
    with pytest.raises(Problem, match="invalid_request"):
        await manager.pair(speaker, "192.168.1.176", WORD)
    registry.async_update_entity(answer, original_name="Answer call")
    link = FakeLink()
    with patch("custom_components.roomcall.echo.EchoLink.connect", AsyncMock(return_value=link)):
        call_id = manager.start(speaker, "owner", "Phone", lambda _: None)
        await until(lambda: manager.calls[call_id].link)
        await manager.remove(speaker)
        await until(lambda: link.close.await_count)
    assert not manager.calls


async def test_invitation_limits_expiry_and_unload(hass, paired):
    from datetime import timedelta

    from homeassistant.util import dt as dt_util
    from pytest_homeassistant_custom_component.common import async_fire_time_changed

    manager, speaker, _ = paired
    station = Station("browser", "owner", "kitchen", "Kitchen", lambda _: None)
    with pytest.raises(Problem, match="station_unavailable"):
        manager.invite("missing", station)
    for _ in range(16):
        manager.invite(speaker, station)
    with pytest.raises(Problem, match="queue_full"):
        manager.invite(speaker, station)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert not manager.invitations
    manager.invite(speaker, station)
    await manager.close()
    assert not manager.invitations


async def test_native_sentences_announcements_and_unknown_rooms(hass, setup_roomcall):
    from custom_components.roomcall.core import area_id_for
    from homeassistant.components.conversation import async_converse
    from homeassistant.core import Context

    async def say(text, language="en"):
        result = await async_converse(hass, text, None, Context(), language=language)
        return result.response.as_dict()["speech"]["plain"]["speech"]

    with patch(
        "custom_components.roomcall.voice.execute", AsyncMock(return_value={"ok": True})
    ) as send:
        assert "Kitchen" in await say("announce to Kitchen that dinner is ready")
        args = send.call_args.args
        assert args[1] == "send"
        assert args[2]["areas"] == [area_id_for(hass, "Kitchen")]
        assert args[2]["message"] == "dinner is ready"
        assert "Kitchen" in await say("room call Kitchen")
        await say("roomcall nonexistent")
        assert send.await_count == 1
    with patch(
        "custom_components.roomcall.voice.execute",
        AsyncMock(return_value={"ok": False, "error": {"message": "Not allowed"}}),
    ):
        assert await say("announce to Kitchen that dinner is ready") == "Not allowed"


async def test_native_voice_echo_to_browser_invitation_and_permissions(hass, paired):
    from homeassistant.components.conversation import async_converse
    from homeassistant.core import Context

    manager, speaker, _ = paired
    registry = er.async_get(hass)
    device = registry.async_get(speaker).device_id
    satellite = registry.async_get_or_create(
        "assist_satellite", "esphome", "echo-satellite", device_id=device
    ).entity_id
    events = []
    manager.data.stations.register(Station("browser", "owner", "kitchen", "Kitchen", events.append))
    result = await async_converse(
        hass, "roomcall Kitchen", None, Context(), language="en", satellite_id=satellite
    )
    assert "Calling" in result.response.as_dict()["speech"]["plain"]["speech"]
    assert events[-1]["type"] == "echo_incoming"
    with patch.object(manager, "invite", side_effect=Problem("queue_full")):
        result = await async_converse(
            hass, "roomcall Kitchen", None, Context(), language="en", satellite_id=satellite
        )
        assert "Calling" not in result.response.as_dict()["speech"]["plain"]["speech"]
    with patch("custom_components.roomcall.voice.permitted", AsyncMock(return_value=False)):
        result = await async_converse(
            hass, "roomcall Kitchen", None, Context(), language="en", satellite_id=satellite
        )
        assert "Calling" not in result.response.as_dict()["speech"]["plain"]["speech"]
    assert len(manager.invitations) == 1


async def test_echo_websocket_pairing_call_audio_hangup(hass, paired, hass_ws_client, pcm):
    manager, speaker, _ = paired
    socket = await hass_ws_client(hass)
    await socket.send_json(
        {
            "id": 1,
            "type": "roomcall/echo_pair",
            "speaker": speaker,
            "host": "192.168.1.176",
            "word": WORD,
        }
    )
    result = await socket.receive_json()
    assert result["success"] and WORD not in str(result)
    link = FakeLink()
    with patch("custom_components.roomcall.echo.EchoLink.connect", AsyncMock(return_value=link)):
        await socket.send_json({"id": 2, "type": "roomcall/echo_call", "speaker": speaker})
        messages = [await socket.receive_json(), await socket.receive_json()]
        result = next(message for message in messages if message["type"] == "result")
        assert result["success"]
        call_id = result["result"]["id"]
        assert any(message.get("event", {}).get("type") == "calling" for message in messages)
        await link.queue.put((b"A", b""))
        assert (await socket.receive_json())["event"]["type"] == "accepted"
        await socket.send_json(
            {
                "id": 3,
                "type": "roomcall/echo_audio",
                "call_id": call_id,
                "pcm": base64.b64encode(pcm).decode(),
            }
        )
        assert (await socket.receive_json())["success"]
        assert link.send.await_count == 1
        link.send.side_effect = OSError("private address")
        await socket.send_json(
            {
                "id": 4,
                "type": "roomcall/echo_audio",
                "call_id": call_id,
                "pcm": base64.b64encode(pcm).decode(),
            }
        )
        result = await socket.receive_json()
        assert not result["success"] and "private" not in str(result)
        await socket.send_json(
            {"id": 5, "type": "roomcall/echo_audio", "call_id": call_id, "hangup": True}
        )
        results = [await socket.receive_json(), await socket.receive_json()]
        assert any(r.get("success") for r in results)
        assert any(r.get("event", {}).get("type") == "ended" for r in results)
        await until(lambda: not manager.calls)
    await socket.send_json(
        {"id": 6, "type": "roomcall/echo_pair", "speaker": speaker, "remove": True}
    )
    assert (await socket.receive_json())["success"]
    await socket.send_json({"id": 7, "type": "roomcall/echo_call", "speaker": speaker})
    assert not (await socket.receive_json())["success"]
    await socket.send_json(
        {"id": 8, "type": "roomcall/echo_pair", "speaker": speaker, "host": "8.8.8.8", "word": WORD}
    )
    assert not (await socket.receive_json())["success"]
    await socket.close()


async def test_echo_ws_authorization_and_not_ready(hass, paired):
    from unittest.mock import Mock

    from custom_components.roomcall.ws import (
        ws_echo_audio,
        ws_echo_call,
        ws_echo_decline,
        ws_echo_pair,
        ws_echo_station,
    )

    socket = Mock()
    socket.user = Mock(is_admin=False)
    await ws_echo_pair.__wrapped__(hass, socket, {"id": 1})
    assert socket.send_error.call_args.args[1] == "forbidden"
    socket.user.is_admin = True
    with patch("custom_components.roomcall.communication.permitted", AsyncMock(return_value=False)):
        await ws_echo_call.__wrapped__(hass, socket, {"id": 1, "speaker": paired[1]})
        assert socket.send_error.call_args.args[1] == "forbidden"
    data = hass.data.pop("roomcall")
    for handler in (ws_echo_pair, ws_echo_call, ws_echo_audio, ws_echo_station, ws_echo_decline):
        await handler.__wrapped__(hass, socket, {"id": 1})
        assert socket.send_error.call_args.args[1] == "not_ready"
    hass.data["roomcall"] = data


async def test_native_station_ownership_persistence_notifications_and_forget(hass, paired, house):
    from custom_components.roomcall.echo_devices import EchoDevices

    manager, speaker, _ = paired
    devices = manager.devices
    phone = MockConfigEntry(
        domain="mobile_app", data={"device_name": "Owner phone", "user_id": "owner"}
    )
    phone.add_to_hass(hass)
    other = MockConfigEntry(
        domain="mobile_app", data={"device_name": "Other phone", "user_id": "other"}
    )
    other.add_to_hass(hass)
    mine = async_mock_service(hass, "notify", "mobile_app_owner_phone")
    theirs = async_mock_service(hass, "notify", "mobile_app_other_phone")
    manager.data.notify = [
        "notify.mobile_app_owner_phone",
        "notify.mobile_app_other_phone",
        "notify.absent",
    ]
    await devices.register("native-phone", "owner", house["kitchen"], "Phone")
    with pytest.raises(Problem, match="forbidden"):
        await devices.register("native-phone", "other", "", "Phone")
    with pytest.raises(Problem, match="no_targets"):
        await devices.register("native-phone", "owner", "missing", "Phone")
    restored = EchoDevices(hass, manager.data)
    await restored.load()
    assert restored.items == devices.items
    assert not restored.targets("missing")
    events = []
    devices.listeners["native-phone"] = events.append
    invitation = manager.invite(speaker, devices.targets(house["kitchen"])[0])
    await until(lambda: bool(mine))
    assert len(mine) == 1 and not theirs
    payload = mine[0].data["data"]
    assert payload["url"] == payload["actions"][0]["uri"]
    assert payload["url"].startswith("roomcall://call?station=native-phone&invitation=")
    assert invitation in payload["url"] and WORD not in str(mine[0].data)
    assert events[-1]["type"] == "echo_incoming" and not manager.calls
    with pytest.raises(Problem, match="forbidden"):
        manager.cancel_invite(invitation, "other")
    manager.cancel_invite(invitation, "owner")
    assert events[-1] == {"type": "echo_cancelled", "id": invitation}
    with pytest.raises(Problem, match="not_found"):
        manager.cancel_invite(invitation, "owner")
    await devices.register("native-phone", "owner", "", "Phone")
    await devices.notify("native-phone", {"id": "stale", "from": "Echo"})
    assert not devices.items
    devices.items = {
        str(i): {"owner": "owner", "room": house["kitchen"], "name": "Phone"} for i in range(64)
    }
    with pytest.raises(Problem, match="queue_full"):
        await devices.register("extra-phone", "owner", house["kitchen"], "Phone")


async def test_native_station_websocket_reconnect_pending_decline_and_close(
    hass, paired, house, hass_ws_client
):
    manager, speaker, _ = paired
    socket = await hass_ws_client(hass)
    request = {
        "type": "roomcall/echo_station",
        "station_id": "native-phone",
        "room": house["kitchen"],
    }
    await socket.send_json({"id": 1, **request})
    result = await socket.receive_json()
    assert result["success"] and result["result"]["echoes"][0]["speaker"] == speaker
    target = manager.devices.targets(house["kitchen"])[0]
    invitation = manager.invite(speaker, target)
    event = await socket.receive_json()
    assert event["event"]["id"] == invitation
    await socket.close()
    await hass.async_block_till_done()
    assert "native-phone" in manager.devices.items
    assert not manager.devices.listeners and not manager.calls
    socket = await hass_ws_client(hass)
    await socket.send_json({"id": 1, **request})
    result = await socket.receive_json()
    assert result["result"]["invitations"][0]["id"] == invitation
    await socket.send_json({"id": 2, "type": "roomcall/echo_decline", "invitation": invitation})
    messages = [await socket.receive_json(), await socket.receive_json()]
    assert any(m.get("event", {}).get("type") == "echo_cancelled" for m in messages)
    assert any(m.get("id") == 2 and m.get("success") for m in messages)
    assert not manager.invitations and not manager.calls
    await socket.send_json({"id": 3, "type": "roomcall/echo_decline", "invitation": invitation})
    assert (await socket.receive_json())["error"]["code"] == "not_found"
    await socket.send_json({"id": 4, **request, "room": "missing"})
    assert (await socket.receive_json())["error"]["code"] == "no_targets"
    await socket.send_json({"id": 5, **request})
    assert (await socket.receive_json())["success"]
    await socket.send_json({"id": 6, "type": "unsubscribe_events", "subscription": 1})
    assert (await socket.receive_json())["success"]
    assert "native-phone" in manager.devices.listeners  # newer socket subscription survives
    invitation = manager.invite(speaker, manager.devices.targets(house["kitchen"])[0])
    assert (await socket.receive_json())["event"]["id"] == invitation
    await socket.send_json({"id": 7, **request, "room": ""})
    messages = [await socket.receive_json(), await socket.receive_json()]
    assert any(m.get("event", {}).get("type") == "echo_cancelled" for m in messages)
    assert any(m.get("id") == 7 and m.get("success") for m in messages)
    assert not manager.invitations and not manager.devices.items
    await socket.close()
    await hass.async_block_till_done()
    assert not manager.devices.listeners


async def test_voice_can_invite_a_saved_native_station_while_app_is_closed(hass, paired, house):
    from custom_components.roomcall import voice
    from homeassistant.core import Context

    manager, speaker, _ = paired
    registry = er.async_get(hass)
    device = registry.async_get(speaker).device_id
    satellite = registry.async_get_or_create(
        "assist_satellite", "esphome", "native-echo-satellite", device_id=device
    )
    await manager.devices.register("native-phone", "owner", house["kitchen"], "Phone")
    trigger = {"satellite_id": satellite.entity_id, "slots": {"area": "Kitchen"}}
    with patch("custom_components.roomcall.voice.permitted", AsyncMock(return_value=True)):
        response = await voice.call(hass, manager.data, trigger, Context(), house["kitchen"], "en")
    assert "Kitchen" in response
    assert len(manager.invitations) == 1 and not manager.calls
    await manager.close()
