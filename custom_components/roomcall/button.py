"""A "Play now" button for every announcement."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .announcements import Announcer, subentry_device_name
from .const import DOMAIN


def announcement_device(entry: ConfigEntry, subentry_id: str) -> DeviceInfo:
    subentry = entry.subentries[subentry_id]
    return DeviceInfo(
        identifiers={(DOMAIN, subentry_id)},
        name=subentry_device_name(subentry),
        manufacturer="RoomCall",
        model=subentry.subentry_type.title(),
        via_device=(DOMAIN, entry.entry_id),
    )


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    announcer: Announcer = hass.data[DOMAIN].announcer
    for subentry_id in announcer.items:
        async_add_entities(
            [PlayAnnouncementButton(entry, announcer, subentry_id)],
            config_subentry_id=subentry_id,
        )


class PlayAnnouncementButton(ButtonEntity):
    _attr_has_entity_name = True
    _attr_translation_key = "play_announcement"
    _attr_icon = "mdi:play-circle"

    def __init__(self, entry: ConfigEntry, announcer: Announcer, subentry_id: str) -> None:
        self._announcer = announcer
        self._subentry_id = subentry_id
        self._attr_unique_id = f"{subentry_id}_play"
        self._attr_device_info = announcement_device(entry, subentry_id)

    async def async_press(self) -> None:
        await self._announcer.async_play(self._subentry_id)
