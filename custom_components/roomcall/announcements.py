"""Scheduled and triggered announcements, set up as RoomCall config subentries.

* **Scheduled**: a message at a time of day on chosen weekdays ("School bus in 10 minutes").
* **Triggered**: a message when an entity changes to a state, optionally after it has stayed
  that way for a while ("The garage door has been open for 10 minutes").

Messages are templates, rendered when they play, so they can include live values. Each
announcement can target rooms, specific speakers, people (wherever they are), or everywhere.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .const import (
    ATTR_MESSAGE,
    ATTR_PRIORITY,
    ATTR_TARGET_AREAS,
    ATTR_TARGET_PEOPLE,
    ATTR_TARGET_SPEAKERS,
    CONF_COOLDOWN,
    CONF_DAYS,
    CONF_ENTITY,
    CONF_FOR,
    CONF_MESSAGE,
    CONF_PEOPLE,
    CONF_PRIORITY,
    CONF_ROOMS,
    CONF_TARGET_SPEAKERS,
    CONF_TIME,
    CONF_TO_STATE,
    DAYS,
    DEFAULT_COOLDOWN,
    DOMAIN,
    PRIORITY_NORMAL,
    SERVICE_BROADCAST,
    SIGNAL_ANNOUNCEMENT,
    SUBENTRY_SCHEDULED,
    SUBENTRY_TRIGGERED,
)

_LOGGER = logging.getLogger(__name__)


def _seconds(value: Any) -> float:
    """Duration from a duration selector ({hours, minutes, seconds}) or a number."""
    if not value:
        return 0.0
    if isinstance(value, dict):
        return timedelta(**{k: float(v) for k, v in value.items() if v}).total_seconds()
    return float(value)


@dataclass(slots=True)
class Announcement:
    subentry_id: str
    title: str
    kind: str
    data: dict[str, Any]
    enabled: bool = True
    last_played: datetime | None = None
    last_error: str | None = None
    _cooldown_until: float = 0.0
    _pending: Callable[[], None] | None = field(default=None, repr=False)

    def summary(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "enabled": self.enabled,
            "message": self.data.get(CONF_MESSAGE),
            "rooms": self.data.get(CONF_ROOMS) or [],
            "speakers": self.data.get(CONF_TARGET_SPEAKERS) or [],
            "people": self.data.get(CONF_PEOPLE) or [],
            "priority": self.data.get(CONF_PRIORITY, PRIORITY_NORMAL),
            "last_played": self.last_played.isoformat() if self.last_played else None,
            "last_error": self.last_error,
            **(
                {"time": self.data.get(CONF_TIME), "days": self.data.get(CONF_DAYS) or list(DAYS)}
                if self.kind == SUBENTRY_SCHEDULED
                else {
                    "entity": self.data.get(CONF_ENTITY),
                    "to_state": self.data.get(CONF_TO_STATE),
                    "for_seconds": _seconds(self.data.get(CONF_FOR)),
                }
            ),
        }


class Announcer:
    """Runs every announcement of one RoomCall entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.hass = hass
        self.items: dict[str, Announcement] = {
            sid: Announcement(sid, sub.title, sub.subentry_type, dict(sub.data))
            for sid, sub in entry.subentries.items()
            if sub.subentry_type in (SUBENTRY_SCHEDULED, SUBENTRY_TRIGGERED)
        }
        self._unsubs: list[Callable[[], None]] = []

    @callback
    def async_start(self) -> None:
        for item in self.items.values():
            if item.kind == SUBENTRY_SCHEDULED:
                self._schedule(item)
            else:
                self._watch(item)

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        for item in self.items.values():
            if item._pending is not None:
                item._pending()
                item._pending = None

    def _schedule(self, item: Announcement) -> None:
        at = dt_util.parse_time(str(item.data.get(CONF_TIME) or ""))
        if at is None:
            _LOGGER.warning("RoomCall announcement %s has no valid time", item.title)
            return
        days = set(item.data.get(CONF_DAYS) or DAYS)

        @callback
        def fire(now: datetime) -> None:
            if item.enabled and DAYS[dt_util.as_local(now).weekday()] in days:
                self.hass.async_create_task(self.async_play(item.subentry_id))

        self._unsubs.append(
            async_track_time_change(
                self.hass, fire, hour=at.hour, minute=at.minute, second=at.second
            )
        )

    def _watch(self, item: Announcement) -> None:
        entity = item.data.get(CONF_ENTITY)
        if not entity:
            return
        to_state = (item.data.get(CONF_TO_STATE) or "").strip()
        hold = _seconds(item.data.get(CONF_FOR))
        cooldown = float(item.data.get(CONF_COOLDOWN, DEFAULT_COOLDOWN) or 0)

        def matches(state: Any) -> bool:
            if state is None or state.state in ("unavailable", "unknown"):
                return False
            return not to_state or state.state == to_state

        @callback
        def changed(event: Event[EventStateChangedData]) -> None:
            new, old = event.data["new_state"], event.data["old_state"]
            if item._pending is not None:  # condition broke while waiting
                if not matches(new):
                    item._pending()
                    item._pending = None
                return
            if not item.enabled or not matches(new):
                return
            if old is not None and new is not None and old.state == new.state:
                return  # attribute-only change
            variables = {
                "entity": entity,
                "name": new.name if new else entity,
                "state": new.state if new else None,
                "from_state": old.state if old else None,
            }

            @callback
            def go(_now: datetime | None = None) -> None:
                item._pending = None
                current = self.hass.states.get(entity)
                if not item.enabled or not matches(current):
                    return
                now = dt_util.utcnow().timestamp()
                if now < item._cooldown_until:
                    return
                item._cooldown_until = now + cooldown
                self.hass.async_create_task(self.async_play(item.subentry_id, variables))

            if hold:
                item._pending = async_call_later(self.hass, hold, go)
            else:
                go()

        self._unsubs.append(async_track_state_change_event(self.hass, [entity], changed))

    async def async_play(
        self, subentry_id: str, variables: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Play one announcement now (used by schedules, triggers and the Play button)."""
        item = self.items[subentry_id]
        message = Template(str(item.data.get(CONF_MESSAGE) or ""), self.hass).async_render(
            variables or {}, parse_result=False
        )
        call: dict[str, Any] = {
            ATTR_MESSAGE: message,
            ATTR_PRIORITY: item.data.get(CONF_PRIORITY, PRIORITY_NORMAL),
        }
        if rooms := item.data.get(CONF_ROOMS):
            call[ATTR_TARGET_AREAS] = rooms
        if speakers := item.data.get(CONF_TARGET_SPEAKERS):
            call[ATTR_TARGET_SPEAKERS] = speakers
        if people := item.data.get(CONF_PEOPLE):
            call[ATTR_TARGET_PEOPLE] = people
        try:
            result = await self.hass.services.async_call(
                DOMAIN, SERVICE_BROADCAST, call, blocking=True, return_response=True
            )
            item.last_error = None
        except HomeAssistantError as err:
            item.last_error = str(err)
            _LOGGER.warning("RoomCall announcement %s not played: %s", item.title, err)
            result = {"ok": False, "error": str(err)}
        item.last_played = dt_util.utcnow()
        async_dispatcher_send(self.hass, f"{SIGNAL_ANNOUNCEMENT}_{subentry_id}")
        return result or {}


def subentry_device_name(subentry: ConfigSubentry) -> str:
    return f"Announcement: {subentry.title}"
