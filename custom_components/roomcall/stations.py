"""Authenticated browser station directory and consent-based WebRTC signaling."""

from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.event import async_call_later

from .errors import Problem


@dataclass
class Station:
    id: str
    owner: str
    room: str
    name: str
    emit: Callable[[dict[str, Any]], None]


class Stations:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.items: dict[str, Station] = {}
        self.sessions: dict[str, dict[str, Any]] = {}

    def list(self) -> list[dict[str, str]]:
        return [
            {
                "id": s.id,
                "room": s.room,
                "name": s.name,
                "capability": "webrtc_audio",
                "consent": "required",
            }
            for s in self.items.values()
        ]

    def register(self, station: Station) -> None:
        if len(self.items) >= 64:
            raise Problem("queue_full")
        if station.id in self.items:
            raise Problem("station_in_use")
        if ar.async_get(self.hass).async_get_area(station.room) is None:
            raise Problem("no_targets")
        self.items[station.id] = station

    def owned(self, station_id: str, owner: str) -> Station:
        station = self.items.get(station_id)
        if station is None:
            raise Problem("station_unavailable")
        if station.owner != owner:
            raise Problem("forbidden")
        return station

    def invite(self, station_id: str, peer: str, owner: str) -> str:
        caller = self.owned(station_id, owner)
        if peer not in self.items or peer == station_id:
            raise Problem("station_unavailable")
        if any(station_id in s["members"] or peer in s["members"] for s in self.sessions.values()):
            raise Problem("station_busy")
        session_id = secrets.token_urlsafe(24)
        self.sessions[session_id] = {
            "members": (station_id, peer),
            "accepted": False,
            "cancel_timer": async_call_later(
                self.hass, 60, lambda _: self.end(session_id, "timeout")
            ),
        }
        self.items[peer].emit(
            {"type": "incoming", "id": session_id, "from": caller.name, "room": caller.room}
        )
        caller.emit({"type": "calling", "id": session_id})
        return session_id

    def action(
        self,
        station_id: str,
        owner: str,
        session_id: str,
        action: str,
        signal: dict[str, Any] | None = None,
    ) -> None:
        self.owned(station_id, owner)
        session = self.sessions.get(session_id)
        if session is None:
            raise Problem("not_found")
        if station_id not in session["members"]:
            raise Problem("forbidden")
        caller, callee = session["members"]
        peer = callee if station_id == caller else caller
        if action == "hangup":
            self.end(session_id, "ended")
        elif action == "answer":
            if station_id != callee or session["accepted"]:
                raise Problem("forbidden")
            session["accepted"] = True
            session["cancel_timer"]()
            session["cancel_timer"] = async_call_later(
                self.hass, 900, lambda _: self.end(session_id, "timeout")
            )
            self.items[caller].emit({"type": "accepted", "id": session_id})
        elif action == "signal":
            if not session["accepted"]:
                raise Problem("consent_required")
            if not isinstance(signal, dict) or len(json.dumps(signal)) > 65536:
                raise Problem("invalid_request")
            if set(signal) not in ({"description"}, {"candidate"}):
                raise Problem("invalid_request")
            self.items[peer].emit({"type": "signal", "id": session_id, "signal": signal})
        else:
            raise Problem("invalid_request")

    def end(self, session_id: str, reason: str) -> None:
        session = self.sessions.pop(session_id, None)
        if session:
            session["cancel_timer"]()
            for member in session["members"]:
                if member in self.items:
                    self.items[member].emit({"type": "ended", "id": session_id, "reason": reason})

    def remove(self, station_id: str) -> None:
        for key, session in list(self.sessions.items()):
            if station_id in session["members"]:
                self.end(key, "disconnected")
        self.items.pop(station_id, None)

    def close(self) -> None:
        for key in list(self.sessions):
            self.end(key, "disconnected")
        self.items.clear()
