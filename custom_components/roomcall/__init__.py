"""RoomCall: push-to-talk intercom over Home Assistant media players."""

from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass, field
from datetime import time
from pathlib import Path
from typing import Any

from homeassistant.components import persistent_notification
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from . import chimes, frontend, intents, localization, repairs, ws
from .announcements import Announcer
from .calls import CallError
from .communication import SCHEMAS, execute, task_permitted
from .const import (
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
from .errors import Problem
from .http import (
    ChimeView,
    ClipView,
    CommandView,
    HistoryClipView,
    HistoryView,
    IOSClientView,
    LiveView,
    LocaleView,
    RoomsView,
    ShortcutView,
    TalkView,
)
from .jobs import Jobs
from .live import LiveStore
from .messages import send
from .onboarding import Setup
from .policy import QuietHours
from .protocol import SETUP_KEY
from .scheduler import Scheduler
from .schema import vol
from .stations import Stations
from .telemetry import Evidence, Metrics
from .voicemail import Mailbox

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
    aliases: dict[str, str] = field(default_factory=dict)
    preferences: dict[str, dict[str, Any]] = field(default_factory=dict)
    adapters: dict[str, dict[str, str]] = field(default_factory=dict)
    presence_max_age: int = 300
    presence_min_confidence: float = 0
    max_volume: float = 1
    voicemail_enabled: bool = False
    voicemail_retention: int = 86400
    translation_entity: str | None = None
    audio_processing: str = "off"
    metrics: Metrics = field(default_factory=Metrics)
    evidence: Evidence = field(init=False)
    scheduler: Scheduler | None = None
    jobs: Jobs | None = None
    stations: Stations | None = None
    mailbox: Mailbox | None = None
    sound_tests: dict[str, Any] = field(default_factory=dict)
    tests: dict[str, tuple[str, float, str | None]] = field(default_factory=dict)
    setup_store: Any = None

    def __post_init__(self) -> None:
        self.evidence = Evidence(self.metrics)


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
    communication = opts.get("communication") or {}
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
        aliases=dict(communication.get("aliases") or {}),
        preferences=dict(communication.get("preferences") or {}),
        adapters=dict(communication.get("adapters") or {}),
        presence_max_age=int(communication.get("presence_max_age", 300)),
        presence_min_confidence=float(communication.get("presence_min_confidence", 0)),
        max_volume=float(communication.get("max_volume", 1)),
        voicemail_enabled=bool(communication.get("voicemail", False)),
        voicemail_retention=int(communication.get("voicemail_retention", 86400)),
        translation_entity=communication.get("translation_entity"),
        audio_processing=communication.get("audio_processing", "off"),
    )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Register HTTP views, the dashboard card and the broadcast action once."""
    await localization.async_load(hass)
    setup = Setup(hass)
    await setup.load()
    hass.data[SETUP_KEY] = setup
    for view in (
        TalkView,
        RoomsView,
        ShortcutView,
        ClipView,
        LiveView,
        ChimeView,
        HistoryView,
        HistoryClipView,
        CommandView,
        LocaleView,
        IOSClientView,
    ):
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
        try:
            record = await send(hass, data, dict(call.data), call.context)
        except (CallError, Problem) as err:
            raise ServiceValidationError(str(err)) from err
        return {
            "ok": record.ok,
            "id": record.call_id,
            "results": [r.as_dict() for r in record.results],
            "skipped": record.skipped,
            "evidence": record.evidence,
            "audible": None,
            "delivery_semantics": "request_accepted",
        }

    hass.services.async_register(
        DOMAIN,
        SERVICE_BROADCAST,
        _broadcast,
        schema=BROADCAST_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

    async def _communicate(call: ServiceCall) -> ServiceResponse:
        return await execute(
            hass, call.data["operation"], call.data.get("request", {}), call.context
        )

    hass.services.async_register(
        DOMAIN,
        "communicate",
        _communicate,
        schema=vol.Schema(
            {vol.Required("operation"): vol.In(SCHEMAS), vol.Optional("request", default={}): dict}
        ),
        supports_response=SupportsResponse.OPTIONAL,
    )
    from homeassistant.helpers import llm

    from .llm import RoomCallAPI

    llm.async_register_api(hass, RoomCallAPI(hass))
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    data = settings_from_options({**entry.data, **entry.options})
    if kept := hass.data.pop(_KEPT, None):  # history and Do Not Disturb survive a reload
        data.history, data.dnd = kept
    data.entry_id = entry.entry_id
    data.scheduler = Scheduler(hass, data.aliases)
    data.jobs = Jobs(hass, data)
    data.stations = Stations(hass)
    data.mailbox = Mailbox(hass, entry.entry_id, data.voicemail_enabled, data.voicemail_retention)
    await data.mailbox.load()
    data.setup_store = Store(hass, 1, f"roomcall.{entry.entry_id}.setup")
    data.sound_tests = (await data.setup_store.async_load() or {}).get("sound_tests", {})

    async def notification_action(event) -> None:
        action = event.data.get("action", "")
        if not isinstance(action, str):
            return
        # Only these explicit actions count; dismissing a notification sends none.
        for prefix in ("ROOMCALL_TASK_DONE_", "ROOMCALL_TASK_SNOOZE_"):
            if action.startswith(prefix):
                task_id = action.removeprefix(prefix)
                if not await task_permitted(hass, task_id, event.context):
                    return
                with contextlib.suppress(Problem):
                    if prefix == "ROOMCALL_TASK_DONE_":
                        await data.announcer.async_complete(task_id, event.context)
                    else:
                        await data.announcer.async_snooze(task_id)
                return
        if action.startswith("ROOMCALL_ACK_") and event.context.user_id:
            with contextlib.suppress(Problem):
                await data.jobs.acknowledge(action.removeprefix("ROOMCALL_ACK_"), event.context, "")

    entry.async_on_unload(
        hass.bus.async_listen("mobile_app_notification_action", notification_action)
    )
    hass.data[DOMAIN] = data
    data.announcer = Announcer(hass, entry)
    await data.announcer.async_load()
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
    if current is not None:
        await current.jobs.close()
        await current.live.close()
        await current.scheduler.close()
        current.stations.close()
        current.evidence.close()
        current.mailbox.close()
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded and (data := hass.data.pop(DOMAIN, None)) is not None:
        hass.data[_KEPT] = (data.history, data.dnd)
    return unloaded
