"""Station consent, ownership, signaling, disconnect and expiry boundaries."""

from __future__ import annotations

from datetime import timedelta

import pytest
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.stations import Station
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import async_fire_time_changed


async def test_session_consent_ownership_signaling_and_disconnect(hass, setup_roomcall):
    stations = hass.data["roomcall"].stations
    a_events, b_events = [], []
    stations.register(Station("station_a", "a", "kitchen", "Kitchen", a_events.append))
    stations.register(Station("station_b", "b", "basement", "Basement", b_events.append))
    assert len(stations.list()) == 2
    with pytest.raises(Problem, match="station_in_use"):
        stations.register(Station("station_a", "other", "kitchen", "Other", a_events.append))
    with pytest.raises(Problem, match="no_targets"):
        stations.register(Station("invalid", "a", "missing", "Other", a_events.append))
    with pytest.raises(Problem, match="forbidden"):
        stations.invite("station_a", "station_b", "b")
    with pytest.raises(Problem, match="station_unavailable"):
        stations.invite("station_a", "missing", "a")
    session_id = stations.invite("station_a", "station_b", "a")
    assert b_events[-1]["type"] == "incoming"
    with pytest.raises(Problem, match="station_busy"):
        stations.invite("station_a", "station_b", "a")
    with pytest.raises(Problem, match="consent_required"):
        stations.action("station_a", "a", session_id, "signal", {"description": {}})
    with pytest.raises(Problem, match="forbidden"):
        stations.action("station_a", "a", session_id, "answer")
    stations.action("station_b", "b", session_id, "answer")
    assert a_events[-1]["type"] == "accepted"
    stations.action("station_a", "a", session_id, "signal", {"description": {"type": "offer"}})
    assert b_events[-1]["signal"]["description"]["type"] == "offer"
    for invalid in ({"unknown": {}}, {"description": "x" * 65537}):
        with pytest.raises(Problem, match="invalid_request"):
            stations.action("station_a", "a", session_id, "signal", invalid)
    stations.remove("station_b")
    assert a_events[-1]["reason"] == "disconnected"
    assert not stations.sessions
    with pytest.raises(Problem, match="not_found"):
        stations.action("station_a", "a", session_id, "hangup")


async def test_station_ringing_and_connected_calls_expire(hass, setup_roomcall):
    stations = hass.data["roomcall"].stations
    events = []
    for suffix in ("a", "b"):
        stations.register(Station(suffix, suffix, "kitchen", suffix, events.append))
    stations.invite("a", "b", "a")
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=61))
    await hass.async_block_till_done()
    assert not stations.sessions and events[-1]["reason"] == "timeout"
    session_id = stations.invite("a", "b", "a")
    stations.action("b", "b", session_id, "answer")
    stations.close()
    assert not stations.items and not stations.sessions


async def test_websocket_registration_and_call_handshake(hass, setup_roomcall, hass_ws_client):
    a, b = await hass_ws_client(hass), await hass_ws_client(hass)
    for socket, station, room in ((a, "browser_a", "kitchen"), (b, "browser_b", "basement")):
        await socket.send_json(
            {"id": 1, "type": "roomcall/station", "station_id": station, "room": room, "name": room}
        )
        assert (await socket.receive_json())["success"]
    await a.send_json(
        {
            "id": 2,
            "type": "roomcall/session",
            "station_id": "browser_a",
            "action": "call",
            "peer": "browser_b",
        }
    )
    incoming = (await b.receive_json())["event"]
    assert incoming["type"] == "incoming"
    assert (await a.receive_json())["event"]["type"] == "calling"
    assert (await a.receive_json())["success"]
    await b.send_json(
        {
            "id": 3,
            "type": "roomcall/session",
            "station_id": "browser_b",
            "action": "answer",
            "session_id": incoming["id"],
        }
    )
    assert (await b.receive_json())["success"]
    assert (await a.receive_json())["event"]["type"] == "accepted"
    await a.send_json(
        {
            "id": 4,
            "type": "roomcall/session",
            "station_id": "browser_a",
            "action": "signal",
            "session_id": incoming["id"],
            "signal": {"candidate": {"candidate": "candidate:test"}},
        }
    )
    assert (await a.receive_json())["success"]
    assert (await b.receive_json())["event"]["signal"]["candidate"]
    await b.close()
    assert (await a.receive_json())["event"]["reason"] == "disconnected"
    await a.close()
