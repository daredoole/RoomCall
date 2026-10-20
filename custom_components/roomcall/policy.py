"""Who hears a call: Do Not Disturb, quiet hours, priority and person targeting."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er

from .const import PRIORITY_URGENT
from .core import area_id_for, friendly, speaker_area


@dataclass(slots=True)
class QuietHours:
    start: time | None = None
    end: time | None = None
    volume: float | None = None
    skip_areas: list[str] = field(default_factory=list)

    def active(self, now: datetime) -> bool:
        if self.start is None or self.end is None or self.start == self.end:
            return False
        current = now.time()
        if self.start < self.end:
            return self.start <= current < self.end
        return current >= self.start or current < self.end  # spans midnight


@dataclass(slots=True)
class Plan:
    entity_ids: list[str]
    volumes: dict[str, float | None]
    skipped: dict[str, str]


class DoNotDisturbError(Exception):
    """Every target is muted by Do Not Disturb or quiet hours."""


def plan(
    hass: HomeAssistant,
    entity_ids: list[str],
    *,
    base_volume: float | None,
    priority: str,
    dnd: bool,
    quiet: QuietHours,
    urgent_volume: float,
    now: datetime,
) -> Plan:
    """Apply priority, Do Not Disturb and quiet hours to the resolved speakers."""
    if priority == PRIORITY_URGENT:
        return Plan(list(entity_ids), dict.fromkeys(entity_ids, urgent_volume), {})
    if dnd:
        raise DoNotDisturbError("Do Not Disturb is on. Send it as urgent to override.")
    if not quiet.active(now):
        return Plan(list(entity_ids), dict.fromkeys(entity_ids, base_volume), {})
    keep, skipped = [], {}
    for entity_id in entity_ids:
        if speaker_area(hass, entity_id) in quiet.skip_areas:
            skipped[friendly(hass, entity_id)] = "quiet hours"
        else:
            keep.append(entity_id)
    if not keep:
        raise DoNotDisturbError(
            "Quiet hours: these rooms are muted. Send it as urgent to override."
        )
    volume = quiet.volume if quiet.volume is not None else base_volume
    return Plan(keep, dict.fromkeys(keep, volume), skipped)


def person_area(hass: HomeAssistant, entity_id: str) -> str | None:
    """Area a person (or tracker/sensor) is in right now.

    Works with room-presence integrations such as Bermuda: a person's device trackers are
    followed to their device, and any ``*_area`` sensor on it whose state names an area wins.
    A sensor or tracker whose own state is an area name is used directly.
    """
    state = hass.states.get(entity_id)
    if state is None:
        return None
    if area := area_id_for(hass, state.state):
        return area
    if (area := state.attributes.get("area")) and (area_id := area_id_for(hass, str(area))):
        return area_id
    trackers = (
        state.attributes.get("device_trackers", []) if entity_id.startswith("person.") else []
    )
    registry = er.async_get(hass)
    for tracker in trackers:
        if (area := person_area(hass, tracker)) is not None:
            return area
        entry = registry.async_get(tracker)
        if entry is None or entry.device_id is None:
            continue
        for sibling in er.async_entries_for_device(registry, entry.device_id):
            if sibling.domain == "sensor" and sibling.entity_id.endswith("_area"):
                sensor = hass.states.get(sibling.entity_id)
                if sensor and (area := area_id_for(hass, sensor.state)):
                    return area
    return None
