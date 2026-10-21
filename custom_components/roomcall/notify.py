"""One notify entity per room: ``notify.send_message`` speaks the message in that room."""

from __future__ import annotations

from homeassistant.components.notify import NotifyEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SERVICE_BROADCAST
from .core import rooms


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    data = hass.data[DOMAIN]
    async_add_entities(RoomNotify(entry, room) for room in rooms(hass, data.speakers))


class RoomNotify(NotifyEntity):
    """Send a message to a room: it is spoken there, chime first."""

    _attr_has_entity_name = True
    _attr_icon = "mdi:account-voice"

    def __init__(self, entry: ConfigEntry, room: dict) -> None:
        self._area_id = room["area_id"]
        self._attr_name = room["name"]
        self._attr_unique_id = f"{entry.entry_id}_notify_{self._area_id}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="RoomCall",
            manufacturer="RoomCall",
            entry_type=DeviceEntryType.SERVICE,
        )

    async def async_send_message(self, message: str, title: str | None = None) -> None:
        text = f"{title}. {message}" if title else message
        try:
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_BROADCAST,
                {"areas": [self._area_id], "message": text},
                blocking=True,
            )
        except Exception as err:
            raise HomeAssistantError(f"RoomCall could not speak in {self.name}: {err}") from err
