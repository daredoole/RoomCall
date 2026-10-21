"""Do Not Disturb switch, and an on/off switch for every announcement."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import STATE_ON
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .announcements import Announcer
from .button import announcement_device
from .const import DOMAIN, SIGNAL_ANNOUNCEMENT


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    async_add_entities([DoNotDisturbSwitch(entry)])
    announcer: Announcer = hass.data[DOMAIN].announcer
    for subentry_id in announcer.items:
        async_add_entities(
            [AnnouncementSwitch(entry, announcer, subentry_id)], config_subentry_id=subentry_id
        )


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


class AnnouncementSwitch(SwitchEntity, RestoreEntity):
    """Turns one scheduled or triggered announcement on or off."""

    _attr_has_entity_name = True
    _attr_translation_key = "announcement_enabled"
    _attr_icon = "mdi:bullhorn"

    def __init__(self, entry: ConfigEntry, announcer: Announcer, subentry_id: str) -> None:
        self._announcer = announcer
        self._item = announcer.items[subentry_id]
        self._attr_unique_id = f"{subentry_id}_enabled"
        self._attr_device_info = announcement_device(entry, subentry_id)

    async def async_added_to_hass(self) -> None:
        if (last := await self.async_get_last_state()) is not None:
            self._item.enabled = last.state == STATE_ON
        self.async_on_remove(
            async_dispatcher_connect(
                self.hass, f"{SIGNAL_ANNOUNCEMENT}_{self._item.subentry_id}", self._updated
            )
        )

    @callback
    def _updated(self) -> None:
        self.async_write_ha_state()

    @property
    def is_on(self) -> bool:
        return self._item.enabled

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return self._item.summary()

    async def async_turn_on(self, **kwargs: Any) -> None:
        self._item.enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self._item.enabled = False
        self.async_write_ha_state()
