"""Sensor that reports the most recent RoomCall broadcast."""

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


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([LastCallSensor(entry)])


class LastCallSensor(SensorEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "last_call"
    _attr_device_class = SensorDeviceClass.TIMESTAMP
    _attr_icon = "mdi:account-voice"

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
        return {
            "target": self._record.target,
            "kind": self._record.kind,
            "ok": self._record.ok,
            "delivered": [r.name for r in self._record.results if r.ok],
            "failed": {r.name: r.error for r in self._record.results if not r.ok},
        }
