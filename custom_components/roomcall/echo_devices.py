"""Saved native Echo stations; Companion notifications open an explicit Answer screen."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from homeassistant.helpers import area_registry as ar
from homeassistant.helpers.storage import Store

from .errors import Problem
from .localization import text
from .replies import _service_users
from .stations import Station


class EchoDevices:
    def __init__(self, hass, data) -> None:
        self.hass, self.data = hass, data
        self.store = Store(hass, 1, f"roomcall.{data.entry_id}.echo_devices")
        self.items: dict[str, dict[str, str]] = {}
        self.listeners: dict[str, Any] = {}

    async def load(self) -> None:
        self.items = await self.store.async_load() or {}

    async def register(self, station_id: str, owner: str, room: str, name: str) -> None:
        if station_id in self.items and self.items[station_id]["owner"] != owner:
            raise Problem("forbidden")
        if not room:
            self.items.pop(station_id, None)
        else:
            if ar.async_get(self.hass).async_get_area(room) is None:
                raise Problem("no_targets")
            if station_id not in self.items and len(self.items) >= 64:
                raise Problem("queue_full")
            self.items[station_id] = {"owner": owner, "room": room, "name": name}
        await self.store.async_save(self.items)

    def targets(self, room: str) -> list[Station]:
        return [
            Station(
                station_id,
                item["owner"],
                room,
                item["name"],
                lambda event, station_id=station_id: self.emit(station_id, event),
            )
            for station_id, item in self.items.items()
            if item["room"] == room
        ]

    def emit(self, station_id: str, event: dict[str, Any]) -> None:
        if listener := self.listeners.get(station_id):
            listener(event)
        if event["type"] == "echo_incoming":
            self.hass.async_create_background_task(
                self.notify(station_id, event), "RoomCall incoming Echo notification"
            )

    async def notify(self, station_id: str, event: dict[str, Any]) -> None:
        # Never accept a client-supplied notify service: only configured Companion
        # devices belonging to this authenticated HA user can receive the invitation.
        users = _service_users(self.hass)
        if station_id not in self.items:
            return
        owner = self.items[station_id]["owner"]
        uri = "roomcall://call?" + urlencode({"station": station_id, "invitation": event["id"]})
        for target in self.data.notify:
            service = target.removeprefix("notify.")
            if users.get(service) == owner and self.hass.services.has_service("notify", service):
                await self.hass.services.async_call(
                    "notify",
                    service,
                    {
                        "title": text(self.hass, "ios_incoming_call"),
                        "message": event["from"],
                        "data": {
                            "push": {"sound": "default"},
                            "tag": event["id"],
                            "group": "roomcall-live",
                            "url": uri,
                            "clickAction": uri,
                            "actions": [
                                {
                                    "action": "URI",
                                    "title": text(self.hass, "ios_open_call"),
                                    "uri": uri,
                                }
                            ],
                        },
                    },
                    blocking=False,
                )
