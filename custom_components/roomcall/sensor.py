"""Sensor that reports the most recent RoomCall broadcast and recent call history."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_CALL_FINISHED
from .core import CallRecord

HISTORY_ATTRIBUTE_SIZE = 10


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([LastCallSensor(entry)])


class LastCallSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "last_call"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:account-voice"
    # The history list changes every call; keep it out of the recorder.
    _unrecorded_attributes = frozenset({"history"})

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_last_call"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="RoomCall",
            manufacturer="RoomCall",
            entry_type=DeviceEntryType.SERVICE,
        )
        self._record: CallRecord | None = None

    async def async_added_to_hass(self) -> None:
        records = self.hass.data[DOMAIN].history.records()
        self._record = records[0] if records else None
        self.async_on_remove(
            async_dispatcher_connect(self.hass, SIGNAL_CALL_FINISHED, self._on_call)
        )

    @callback
    def _on_call(self, record: CallRecord) -> None:
        self._record = record
        self.async_write_ha_state()

    @property
    def native_value(self) -> Any:
        return self._record.at if self._record else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        if self._record is None:
            return None
        summary = self._record.summary()
        history = self.hass.data[DOMAIN].history.records()[:HISTORY_ATTRIBUTE_SIZE]
        return {
            "target": summary["target"],
            "from": summary["from"],
            "kind": summary["kind"],
            "priority": summary["priority"],
            "ok": summary["ok"],
            "delivered": summary["delivered"],
            "failed": summary["failed"],
            "skipped": summary["skipped"],
            "text": summary["text"],
            "history": [r.summary() for r in history],
        }
