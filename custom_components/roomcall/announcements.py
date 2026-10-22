"""Scheduled and triggered announcements, set up as RoomCall config subentries.

* **Scheduled**: a message at a time of day on chosen weekdays ("School bus in 10 minutes").
* **Triggered**: a message when an entity changes to a state, optionally after it has stayed
  that way for a while ("The garage door has been open for 10 minutes").

Messages are templates, rendered when they play, so they can include live values. Each
announcement can target rooms, specific speakers, people (wherever they are), or everywhere.

An announcement with a **task** ("Took out the dry clothes") stays open after it plays and
repeats until someone confirms it was done. Dismissing a phone notification is not
confirmation; only the task action, the card, the button entity or the API complete it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigSubentry
from homeassistant.core import Context, Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.helpers.event import (
    async_call_later,
    async_track_state_change_event,
    async_track_time_change,
)
from homeassistant.helpers.storage import Store
from homeassistant.helpers.template import Template
from homeassistant.util import dt as dt_util

from .calls import CallError
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
    CONF_REMIND_EVERY,
    CONF_ROOMS,
    CONF_TARGET_SPEAKERS,
    CONF_TASK,
    CONF_TIME,
    CONF_TO_STATE,
    DAYS,
    DEFAULT_COOLDOWN,
    DEFAULT_REMIND_EVERY,
    DEFAULT_SNOOZE,
    DOMAIN,
    EVENT_TASK_COMPLETED,
    PRIORITY_NORMAL,
    PRIORITY_URGENT,
    SERVICE_BROADCAST,
    SIGNAL_ANNOUNCEMENT,
    SUBENTRY_SCHEDULED,
    SUBENTRY_TRIGGERED,
)
from .errors import Problem
from .messages import send
from .replies import clear_task, notify_task

_LOGGER = logging.getLogger(__name__)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


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
    task_opened: datetime | None = None
    task_done: datetime | None = None
    task_done_by: str | None = None
    _variables: dict[str, Any] = field(default_factory=dict, repr=False)
    _reminder: Callable[[], None] | None = field(default=None, repr=False)

    @property
    def task(self) -> str | None:
        return str(self.data.get(CONF_TASK) or "").strip() or None

    @property
    def task_state(self) -> str:
        return "open" if self.task_opened else "done" if self.task_done else "idle"

    def task_summary(self) -> dict[str, Any]:
        return {
            "id": self.subentry_id,
            "name": self.title,
            "task": self.task,
            "state": self.task_state,
            "opened": _iso(self.task_opened),
            "done": _iso(self.task_done),
            "done_by": self.task_done_by,
            "remind_every": self.data.get(CONF_REMIND_EVERY, DEFAULT_REMIND_EVERY),
        }

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
                {
                    "task": self.task,
                    "task_state": self.task_state,
                    "task_opened": _iso(self.task_opened),
                    "task_done": _iso(self.task_done),
                    "task_done_by": self.task_done_by,
                }
                if self.task
                else {}
            ),
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
        self._stopped = False  # a reload replaces this announcer; plays in flight must not remind
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"roomcall.{entry.entry_id}.tasks")

    async def async_load(self) -> None:
        """Open tasks survive restarts and reloads, so reminders carry on."""
        saved_tasks = await self._store.async_load() or {}
        for sid, saved in saved_tasks.items():
            if (item := self.items.get(sid)) is None or not item.task:
                # Deleted, or no longer a task: forget it and unpin its notification.
                if saved.get("opened"):
                    await clear_task(
                        self.hass, self.hass.data[DOMAIN].notify, subentry_id=sid, people=[]
                    )
                continue
            item.task_opened = dt_util.parse_datetime(saved.get("opened") or "")
            item.task_done = dt_util.parse_datetime(saved.get("done") or "")
            item.task_done_by = saved.get("done_by")
            item._variables = saved.get("variables") or {}
        if set(saved_tasks) - {sid for sid, item in self.items.items() if item.task}:
            await self._save()

    async def _save(self) -> None:
        await self._store.async_save(
            {
                sid: {
                    "opened": _iso(item.task_opened),
                    "done": _iso(item.task_done),
                    "done_by": item.task_done_by,
                    "variables": item._variables,
                }
                for sid, item in self.items.items()
                if item.task
            }
        )

    @callback
    def async_start(self) -> None:
        for item in self.items.values():
            if item.kind == SUBENTRY_SCHEDULED:
                self._schedule(item)
            else:
                self._watch(item)
            if item.task_opened:
                self._remind(item)

    @callback
    def async_stop(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        self._stopped = True
        for item in self.items.values():
            if item._pending is not None:
                item._pending()
                item._pending = None
            self._cancel_reminder(item)

    def _cancel_reminder(self, item: Announcement) -> None:
        if item._reminder is not None:
            item._reminder()
            item._reminder = None

    def _remind(self, item: Announcement, minutes: float | None = None) -> None:
        """Play the announcement again later while its task is open."""
        self._cancel_reminder(item)
        if self._stopped:
            return
        if minutes is None:
            minutes = float(item.data.get(CONF_REMIND_EVERY, DEFAULT_REMIND_EVERY) or 0)
        if not minutes:
            return

        @callback
        def fire(_now: datetime) -> None:
            item._reminder = None
            if item.enabled:
                self.hass.async_create_task(self.async_play(item.subentry_id, reminder=True))
            else:  # paused: keep the task open and check again later
                self._remind(item)

        item._reminder = async_call_later(self.hass, minutes * 60, fire)

    def _task(self, subentry_id: str) -> Announcement:
        item = self.items.get(subentry_id)
        if item is None or not item.task:
            raise Problem("not_found")
        if item.task_opened is None:
            raise Problem("task_not_open")
        return item

    async def _clear(self, item: Announcement) -> None:
        await clear_task(
            self.hass,
            self.hass.data[DOMAIN].notify,
            subentry_id=item.subentry_id,
            people=item.data.get(CONF_PEOPLE) or [],
        )
        async_dispatcher_send(self.hass, f"{SIGNAL_ANNOUNCEMENT}_{item.subentry_id}")

    async def async_complete(self, subentry_id: str, context: Context) -> Announcement:
        """Someone confirms the task was done: stop reminders and tell automations."""
        item = self._task(subentry_id)
        # Claim it before awaiting anything, so simultaneous confirmations complete it once.
        opened = item.task_opened
        item.task_opened = None
        self._cancel_reminder(item)
        user = await self.hass.auth.async_get_user(context.user_id) if context.user_id else None
        item.task_done, item.task_done_by = dt_util.utcnow(), user.name if user else None
        await self._save()
        self.hass.bus.async_fire(
            EVENT_TASK_COMPLETED,
            {
                "announcement_id": subentry_id,
                "name": item.title,
                "task": item.task,
                "user_id": context.user_id,
                "opened": _iso(opened),
            },
            context=context,
        )
        await self._clear(item)
        return item

    async def async_snooze(self, subentry_id: str, minutes: float = DEFAULT_SNOOZE) -> Announcement:
        """Remind again later; the task stays open."""
        item = self._task(subentry_id)
        self._remind(item, minutes)
        await self._clear(item)
        return item

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
        self, subentry_id: str, variables: dict[str, Any] | None = None, *, reminder: bool = False
    ) -> dict[str, Any]:
        """Play one announcement now (used by schedules, triggers and the Play button).

        A reminder only repeats an open task; it never reopens one confirmed meanwhile.
        """
        item = self.items[subentry_id]
        if reminder and item.task_opened is None:
            return {"ok": False, "error": "task_not_open"}
        if item.task:
            # Reminders replay with the values of the change that opened the task.
            if variables is None:
                variables = item._variables
            if item.task_opened is None:
                item.task_opened, item.task_done, item.task_done_by = dt_util.utcnow(), None, None
            item._variables = dict(variables)
            await self._save()
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
            if item.task:
                # The task notification below replaces the generic call notification.
                record = await send(
                    self.hass, self.hass.data[DOMAIN], call, Context(), notify=False
                )
                result = {"ok": record.ok, "id": record.call_id}
            else:
                result = await self.hass.services.async_call(
                    DOMAIN, SERVICE_BROADCAST, call, blocking=True, return_response=True
                )
            item.last_error = None
        except (HomeAssistantError, CallError, Problem) as err:
            item.last_error = str(err)
            _LOGGER.warning("RoomCall announcement %s not played: %s", item.title, err)
            result = {"ok": False, "error": str(err)}
        # Someone may have confirmed it while it played: then there is nothing to remind.
        if item.task and item.task_opened:
            # Phones are told even when no speaker could play: the task still needs doing.
            await notify_task(
                self.hass,
                self.hass.data[DOMAIN].notify,
                subentry_id=subentry_id,
                title=item.title,
                message=message,
                task=item.task,
                people=item.data.get(CONF_PEOPLE) or [],
                urgent=item.data.get(CONF_PRIORITY) == PRIORITY_URGENT,
            )
            self._remind(item)
        item.last_played = dt_util.utcnow()
        async_dispatcher_send(self.hass, f"{SIGNAL_ANNOUNCEMENT}_{subentry_id}")
        return result or {}


def subentry_device_name(subentry: ConfigSubentry) -> str:
    return f"Announcement: {subentry.title}"
