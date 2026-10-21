"""RoomCall: push-to-talk intercom over Home Assistant media players."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

import voluptuous as vol
from homeassistant.components import persistent_notification
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.helpers.start import async_at_started
from homeassistant.util import dt as dt_util

from . import chimes, frontend, intents, repairs, ws
from .announcements import Announcer
from .calls import CallError, CallRequest, place_call
from .const import (
    API_CHIME,
    ATTR_MEDIA_URL,
    ATTR_MESSAGE,
    ATTR_PRIORITY,
    ATTR_TARGET_AREAS,
    ATTR_TARGET_PEOPLE,
    ATTR_TARGET_SPEAKERS,
    ATTR_VOLUME,
    CONF_CHIME,
    CONF_CHIME_STYLE,
    CONF_CHIME_URL,
    CONF_GAIN_DB,
    CONF_HOMEKIT_ENABLED,
    CONF_HOMEKIT_PORT,
    CONF_NOTIFY,
    CONF_QUIET_END,
    CONF_QUIET_SKIP_AREAS,
    CONF_QUIET_START,
    CONF_QUIET_VOLUME,
    CONF_REPLY_PATH,
    CONF_RESTORE,
    CONF_RETRY,
    CONF_SPEAKERS,
    CONF_STAGGER_MS,
    CONF_STT_ENGINE,
    CONF_TTS_ENGINE,
    CONF_URGENT_VOLUME,
    CONF_VOLUME,
    DEFAULT_CHIME,
    DEFAULT_CHIME_STYLE,
    DEFAULT_GAIN_DB,
    DEFAULT_HOMEKIT_PORT,
    DEFAULT_RESTORE,
    DEFAULT_RETRY,
    DEFAULT_STAGGER_MS,
    DEFAULT_URGENT_VOLUME,
    DOMAIN,
    PRIORITIES,
    PRIORITY_NORMAL,
    SECTION_CHIME,
    SECTION_DELIVERY,
    SECTION_HOMEKIT,
    SECTION_QUIET,
    SECTION_REPLY,
    SERVICE_BROADCAST,
    URL_STATIC,
)
from .core import ClipStore, History
from .http import (
    ChimeView,
    ClipView,
    HistoryClipView,
    HistoryView,
    LiveView,
    RoomsView,
    TalkView,
    base_url,
)
from .live import LiveStore
from .policy import QuietHours

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.BUTTON, Platform.NOTIFY, Platform.SENSOR, Platform.SWITCH]
_KEPT = f"{DOMAIN}_kept"
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

BROADCAST_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_TARGET_AREAS): vol.All(cv.ensure_list, [cv.string]),
        vol.Optional(ATTR_TARGET_PEOPLE): cv.entity_ids,
        vol.Optional(ATTR_TARGET_SPEAKERS): cv.entity_ids,
        vol.Exclusive(ATTR_MESSAGE, "content"): cv.string,
        vol.Exclusive(ATTR_MEDIA_URL, "content"): cv.string,
        vol.Optional(ATTR_VOLUME): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
        vol.Optional(ATTR_PRIORITY, default=PRIORITY_NORMAL): vol.In(PRIORITIES),
    }
)


@dataclass(slots=True)
class RoomCallData:
    speakers: list[str]
    volume: float | None
    gain_db: float
    tts_engine: str | None
    chime_style: str | None = DEFAULT_CHIME_STYLE  # None = no chime
    chime_url: str | None = None
    stagger_ms: int = DEFAULT_STAGGER_MS
    retry: bool = DEFAULT_RETRY
    restore: bool = DEFAULT_RESTORE
    urgent_volume: float = DEFAULT_URGENT_VOLUME
    quiet: QuietHours = field(default_factory=QuietHours)
    notify: list[str] = field(default_factory=list)
    reply_path: str | None = None
    stt_engine: str | None = None
    dnd: bool = False
    entry_id: str | None = None
    homekit_enabled: bool = False
    homekit_port: int = DEFAULT_HOMEKIT_PORT
    homekit: Any = None  # HomeKitIntercom while running
    announcer: Any = None  # Announcer for scheduled and triggered announcements
    clips: ClipStore = field(default_factory=ClipStore)
    live: LiveStore = field(default_factory=LiveStore)
    history: History = field(default_factory=History)
    chime_cache: dict[str, bytes] = field(default_factory=dict)


def _time(value: Any) -> time | None:
    if not value:
        return None
    return dt_util.parse_time(str(value))


def _float(value: Any) -> float | None:
    return float(value) if value not in (None, "") else None


def settings_from_options(opts: dict[str, Any]) -> RoomCallData:
    """Build runtime settings from the entry (sections are nested dicts)."""
    chime = opts.get(SECTION_CHIME) or {}
    delivery = opts.get(SECTION_DELIVERY) or {}
    quiet = opts.get(SECTION_QUIET) or {}
    replies = opts.get(SECTION_REPLY) or {}
    homekit = opts.get(SECTION_HOMEKIT) or {}
    style = chime.get(CONF_CHIME_STYLE)
    if style is None:  # entries from 0.1.x only had an on/off switch
        style = DEFAULT_CHIME_STYLE if opts.get(CONF_CHIME, DEFAULT_CHIME) else chimes.OFF
    return RoomCallData(
        speakers=list(opts.get(CONF_SPEAKERS, [])),
        volume=_float(opts.get(CONF_VOLUME)),
        gain_db=float(opts.get(CONF_GAIN_DB, DEFAULT_GAIN_DB)),
        tts_engine=opts.get(CONF_TTS_ENGINE) or None,
        chime_style=None if style == chimes.OFF else style,
        chime_url=(chime.get(CONF_CHIME_URL) or "").strip() or None,
        stagger_ms=int(delivery.get(CONF_STAGGER_MS, DEFAULT_STAGGER_MS)),
        retry=bool(delivery.get(CONF_RETRY, DEFAULT_RETRY)),
        restore=bool(delivery.get(CONF_RESTORE, DEFAULT_RESTORE)),
        urgent_volume=float(delivery.get(CONF_URGENT_VOLUME, DEFAULT_URGENT_VOLUME)),
        quiet=QuietHours(
            start=_time(quiet.get(CONF_QUIET_START)),
            end=_time(quiet.get(CONF_QUIET_END)),
            volume=_float(quiet.get(CONF_QUIET_VOLUME)),
            skip_areas=list(quiet.get(CONF_QUIET_SKIP_AREAS) or []),
        ),
        notify=list(replies.get(CONF_NOTIFY) or []),
        reply_path=(replies.get(CONF_REPLY_PATH) or "").strip() or None,
        stt_engine=replies.get(CONF_STT_ENGINE) or None,
        homekit_enabled=bool(homekit.get(CONF_HOMEKIT_ENABLED, False)),
        homekit_port=int(homekit.get(CONF_HOMEKIT_PORT, DEFAULT_HOMEKIT_PORT)),
    )


def _message_chime(hass: HomeAssistant, data: RoomCallData) -> str | None:
    """Chime TTS ``chime_path`` that matches the push-to-talk chime."""
    if data.chime_url:
        return data.chime_url
    if data.chime_style is None:
        return None
    try:
        return f"{base_url(hass)}{API_CHIME.format(style=data.chime_style)}.mp3"
    except NoURLAvailableError:
        return None


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register HTTP views, the dashboard card and the broadcast action once."""
    for view in (TalkView, RoomsView, ClipView, LiveView, ChimeView, HistoryView, HistoryClipView):
        hass.http.register_view(view())
    ws.async_register(hass)
    intents.async_register(hass)
    await hass.http.async_register_static_paths(
        [StaticPathConfig(URL_STATIC, str(Path(__file__).parent / "frontend"), False)]
    )
    if "frontend" in hass.config.components:

        async def _register_card(_hass: HomeAssistant) -> None:
            try:
                await frontend.async_register_card(hass)
            except Exception:
                _LOGGER.exception("RoomCall could not register its dashboard card")
                add_extra_js_url(hass, frontend.card_url())

        async_at_started(hass, _register_card)

    async def _broadcast(call: ServiceCall) -> ServiceResponse:
        data: RoomCallData | None = hass.data.get(DOMAIN)
        if data is None:
            raise ServiceValidationError("RoomCall is not set up")
        chime_message: dict[str, str] | None = None
        message = call.data.get(ATTR_MESSAGE)
        if message:
            engine = data.tts_engine or next(iter(hass.states.async_entity_ids("tts")), None)
            if engine is None:
                raise ServiceValidationError("No text-to-speech engine is available")
            media_id = f"media-source://tts/{engine}?message={quote(message)}&cache=true"
            kind = "message"
            chime_path = _message_chime(hass, data)
            if chime_path and "chime_tts" in hass.config.components:
                chime_message = {
                    "message": message,
                    "tts_platform": engine,
                    "chime_path": chime_path,
                }
        elif media_url := call.data.get(ATTR_MEDIA_URL):
            media_id, kind = media_url, "media"
        else:
            raise ServiceValidationError("Provide either message or media_url")
        try:
            record = await place_call(
                hass,
                data,
                CallRequest(
                    targets=[
                        *(call.data.get(ATTR_TARGET_AREAS) or []),
                        *(call.data.get(ATTR_TARGET_SPEAKERS) or []),
                    ],
                    people=call.data.get(ATTR_TARGET_PEOPLE),
                    media_id=media_id,
                    kind=kind,
                    priority=call.data[ATTR_PRIORITY],
                    volume=call.data.get(ATTR_VOLUME),
                    message=message,
                    chime_message=chime_message,
                    caller_user_id=call.context.user_id,
                ),
            )
        except CallError as err:
            raise ServiceValidationError(str(err)) from err
        return {
            "ok": record.ok,
            "id": record.call_id,
            "results": [r.as_dict() for r in record.results],
            "skipped": record.skipped,
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_BROADCAST,
        _broadcast,
        schema=BROADCAST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = settings_from_options({**entry.data, **entry.options})
    if kept := hass.data.pop(_KEPT, None):  # history and Do Not Disturb survive a reload
        data.history, data.dnd = kept
    data.entry_id = entry.entry_id
    hass.data[DOMAIN] = data
    data.announcer = Announcer(hass, entry)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    data.announcer.async_start()
    entry.async_on_unload(data.announcer.async_stop)
    # Options and announcements (subentries) both change the entry: reload to apply them.
    entry.async_on_unload(entry.add_update_listener(_reload))
    entry.async_on_unload(repairs.async_watch(hass, data.speakers))
    if data.homekit_enabled:
        await _start_homekit(hass, data)
    return True


async def _start_homekit(hass: HomeAssistant, data: RoomCallData) -> None:
    from .core import rooms
    from .homekit import HomeKitIntercom

    intercom = HomeKitIntercom(hass, data, data.homekit_port)
    try:
        await intercom.async_start(rooms(hass, data.speakers))
    except Exception:
        _LOGGER.exception("RoomCall could not start the Apple Home bridge")
        await intercom.async_stop()
        return
    data.homekit = intercom
    if not intercom.paired:
        persistent_notification.async_create(
            hass,
            "Add each room in the **Home** app: **+** → **Add Accessory** → "
            "**More options…** → pick **<Room> Intercom** and enter the code "
            f"**{intercom.pincode}** (the same code for every room).\n\n"
            "Open a room's camera tile and use the microphone to talk in that room.",
            title="Add RoomCall to Apple Home",
            notification_id=f"{DOMAIN}_homekit_pairing",
        )


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    current: RoomCallData | None = hass.data.get(DOMAIN)
    if current is not None and current.homekit is not None:
        await current.homekit.async_stop()
        current.homekit = None
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and (data := hass.data.pop(DOMAIN, None)) is not None:
        hass.data[_KEPT] = (data.history, data.dnd)
    return unloaded
