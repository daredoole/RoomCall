"""Do Not Disturb switch: while on, only urgent calls play."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([DoNotDisturbSwitch(entry)])


class DoNotDisturbSwitch(SwitchEntity, RestoreEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "do_not_disturb"
    _attr_icon = "mdi:bell-sleep"

    def __init__(self, entry: ConfigEntry) -> None:
        self._attr_unique_id = f"{entry.entry_id}_dnd"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="RoomCall",
            manufacturer="RoomCall",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) is not None:
            self.hass.data[DOMAIN].dnd = last.state == STATE_ON

    @property
    def is_on(self) -> bool:
        return self.hass.data[DOMAIN].dnd

    async def async_turn_on(self, **kwargs: Any) -> None:
        self.hass.data[DOMAIN].dnd = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.hass.data[DOMAIN].dnd = False
        self.async_write_ha_state()
