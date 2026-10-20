"""RoomCall: push-to-talk intercom over Home Assistant media players."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

import voluptuous as vol
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv

from .const import (
    ALL_ROOMS,
    ATTR_MEDIA_URL,
    ATTR_MESSAGE,
    ATTR_TARGET_AREAS,
    ATTR_VOLUME,
    CARD_FILENAME,
    CONF_CHIME,
    CONF_GAIN_DB,
    CONF_SPEAKERS,
    CONF_TTS_ENGINE,
    CONF_VOLUME,
    DEFAULT_CHIME,
    DEFAULT_GAIN_DB,
    DOMAIN,
    MESSAGE_CHIME,
    SERVICE_BROADCAST,
    URL_STATIC,
)
from .core import CallRecord, ClipStore, fan_out, resolve
from .http import ClipView, RoomsView, TalkView

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SENSOR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

BROADCAST_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_TARGET_AREAS): vol.All(cv.ensure_list, [cv.string]),
        vol.Exclusive(ATTR_MESSAGE, "content"): cv.string,
        vol.Exclusive(ATTR_MEDIA_URL, "content"): cv.string,
        vol.Optional(ATTR_VOLUME): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
    }
)


@dataclass(slots=True)
class RoomCallData:
    speakers: list[str]
    volume: float | None
    gain_db: float
    tts_engine: str | None
    chime: bool = DEFAULT_CHIME
    clips: ClipStore = field(default_factory=ClipStore)
    last_call: CallRecord | None = None


def _options(entry: ConfigEntry) -> dict:
    return {**entry.data, **entry.options}


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register HTTP views, the dashboard card and the broadcast action once."""
    hass.http.register_view(TalkView())
    hass.http.register_view(RoomsView())
    hass.http.register_view(ClipView())
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_STATIC, str(Path(__file__).parent / "frontend"), False)]
    )
    if "frontend" in hass.config.components:
        add_extra_js_url(hass, f"{URL_STATIC}/{CARD_FILENAME}?v=0.1.1")

    async def _broadcast(call: ServiceCall) -> ServiceResponse:
        data: RoomCallData | None = hass.data.get(DOMAIN)
        if data is None:
            raise ServiceValidationError("RoomCall is not set up")
        targets = call.data.get(ATTR_TARGET_AREAS) or [ALL_ROOMS]
        chime_message: dict[str, str] | None = None
        entity_ids = resolve(hass, data.speakers, targets)
        if not entity_ids:
            raise ServiceValidationError(f"No RoomCall speakers in {', '.join(targets)}")
        if message := call.data.get(ATTR_MESSAGE):
            engine = data.tts_engine or next(iter(hass.states.async_entity_ids("tts")), None)
            if engine is None:
                raise ServiceValidationError("No text-to-speech engine is available")
            media_id = f"media-source://tts/{engine}?message={quote(message)}&cache=true"
            kind = "message"
            if data.chime and "chime_tts" in hass.config.components:
                chime_message = {
                    "message": message,
                    "tts_platform": engine,
                    "chime_path": MESSAGE_CHIME,
                }
        elif media_url := call.data.get(ATTR_MEDIA_URL):
            media_id, kind = media_url, "media"
        else:
            raise ServiceValidationError("Provide either message or media_url")
        record = await fan_out(
            hass,
            entity_ids,
            media_id,
            call.data.get(ATTR_VOLUME, data.volume),
            target=", ".join(targets),
            kind=kind,
            chime_message=chime_message,
        )
        return {"ok": record.ok, "results": [r.as_dict() for r in record.results]}

    hass.services.async_register(
        DOMAIN,
        SERVICE_BROADCAST,
        _broadcast,
        schema=BROADCAST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    opts = _options(entry)
    volume = opts.get(CONF_VOLUME)
    hass.data[DOMAIN] = RoomCallData(
        speakers=list(opts.get(CONF_SPEAKERS, [])),
        volume=float(volume) if volume not in (None, "") else None,
        gain_db=float(opts.get(CONF_GAIN_DB, DEFAULT_GAIN_DB)),
        tts_engine=opts.get(CONF_TTS_ENGINE) or None,
        chime=bool(opts.get(CONF_CHIME, DEFAULT_CHIME)),
    )
    entry.async_on_unload(entry.add_update_listener(_reload))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data.pop(DOMAIN, None)
    return unloaded
