"""Config and options flow: pick speakers, then tune chimes, delivery, quiet hours and replies."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    ConfigSubentryFlow,
    OptionsFlow,
    SubentryFlowResult,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import section
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector
from homeassistant.setup import async_setup_component

from . import chimes
from .capabilities import deduplicate, default_destination
from .const import (
    CONF_CHIME,
    CONF_CHIME_STYLE,
    CONF_CHIME_URL,
    CONF_COOLDOWN,
    CONF_DAYS,
    CONF_ENTITY,
    CONF_FOR,
    CONF_GAIN_DB,
    CONF_HOMEKIT_ENABLED,
    CONF_HOMEKIT_PORT,
    CONF_MESSAGE,
    CONF_NOTIFY,
    CONF_PEOPLE,
    CONF_PRIORITY,
    CONF_QUIET_END,
    CONF_QUIET_SKIP_AREAS,
    CONF_QUIET_START,
    CONF_QUIET_VOLUME,
    CONF_REMIND_EVERY,
    CONF_REPLY_PATH,
    CONF_RESTORE,
    CONF_RETRY,
    CONF_ROOMS,
    CONF_SPEAKERS,
    CONF_SPEECH_RATE,
    CONF_STAGGER_MS,
    CONF_STT_ENGINE,
    CONF_TARGET_SPEAKERS,
    CONF_TASK,
    CONF_TIME,
    CONF_TO_STATE,
    CONF_TTS_ENGINE,
    CONF_URGENT_VOLUME,
    CONF_VOLUME,
    DAYS,
    DEFAULT_CHIME,
    DEFAULT_CHIME_STYLE,
    DEFAULT_COOLDOWN,
    DEFAULT_HOMEKIT_PORT,
    DEFAULT_REMIND_EVERY,
    DEFAULT_RESTORE,
    DEFAULT_RETRY,
    DEFAULT_SPEECH_RATE,
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
    SUBENTRY_SCHEDULED,
    SUBENTRY_TRIGGERED,
)
from .schema import vol

# Unknown Music Assistant identities are handled by default_destination instead.
_SKIP_PLATFORMS_BY_DEFAULT = {"dlna_dmr", "mobile_app"}


def suggested_speakers(hass: HomeAssistant, current: dict | None = None) -> list[str]:
    from .discovery import players

    recent = players(hass)
    registry = er.async_get(hass)
    communication = (current or {}).get("communication", {})
    aliases = communication.get("aliases", {})
    out = []
    for state in hass.states.async_all("media_player"):
        entry = registry.async_get(state.entity_id)
        if entry and (entry.disabled_by or entry.platform in _SKIP_PLATFORMS_BY_DEFAULT):
            continue
        if (
            recent.recent(state.entity_id)
            and recent.audio_capable(state.entity_id)
            and default_destination(hass, state.entity_id, aliases)
        ):
            out.append(state.entity_id)
    return deduplicate(hass, sorted(out), aliases, communication.get("prefer_music_assistant", []))[
        0
    ]


def _suggest(value: Any) -> dict[str, Any]:
    return {"suggested_value": value} if value not in (None, "", []) else {}


def _volume_slider() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(
            min=0, max=1, step=0.05, mode=selector.NumberSelectorMode.SLIDER
        )
    )


def _main_fields(hass: HomeAssistant, defaults: dict[str, Any]) -> dict[Any, Any]:
    from .discovery import speaker_choices

    choices = speaker_choices(hass, defaults.get(CONF_SPEAKERS, []))
    return {
        vol.Required(
            CONF_SPEAKERS, default=defaults.get(CONF_SPEAKERS, suggested_speakers(hass, defaults))
        ): selector.EntitySelector(
            selector.EntitySelectorConfig(
                domain="media_player",
                multiple=True,
                include_entities=choices,
                exclude_entities=[
                    entity
                    for entity in hass.states.async_entity_ids("media_player")
                    if entity not in choices
                ],
            )
        ),
        vol.Optional(
            CONF_VOLUME, description=_suggest(defaults.get(CONF_VOLUME))
        ): _volume_slider(),
        vol.Optional(CONF_GAIN_DB, default=defaults.get(CONF_GAIN_DB, 0)): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=-10, max=15, step=1, mode=selector.NumberSelectorMode.BOX
            )
        ),
        vol.Optional(
            CONF_TTS_ENGINE, description=_suggest(defaults.get(CONF_TTS_ENGINE))
        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="tts")),
        vol.Optional(
            CONF_SPEECH_RATE,
            description={"suggested_value": defaults.get(CONF_SPEECH_RATE, DEFAULT_SPEECH_RATE)},
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0.5, max=1.5, step=0.05, mode=selector.NumberSelectorMode.SLIDER
            )
        ),
    }


def _notify_targets(hass: HomeAssistant) -> list[str]:
    return sorted(
        f"notify.{name}"
        for name in hass.services.async_services_for_domain("notify")
        if name.startswith("mobile_app_")
    )


def _options_schema(hass: HomeAssistant, current: dict[str, Any]) -> vol.Schema:
    chime = current.get(SECTION_CHIME) or {}
    delivery = current.get(SECTION_DELIVERY) or {}
    quiet = current.get(SECTION_QUIET) or {}
    replies = current.get(SECTION_REPLY) or {}
    homekit = current.get(SECTION_HOMEKIT) or {}
    communication = current.get("communication") or {}
    style = chime.get(CONF_CHIME_STYLE) or (
        DEFAULT_CHIME_STYLE if current.get(CONF_CHIME, DEFAULT_CHIME) else chimes.OFF
    )
    return vol.Schema(
        {
            **_main_fields(hass, current),
            vol.Required(SECTION_CHIME, default=dict(chime)): section(
                vol.Schema(
                    {
                        vol.Required(CONF_CHIME_STYLE, default=style): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=[*chimes.STYLES, chimes.OFF],
                                translation_key="chime_style",
                            )
                        ),
                        vol.Optional(
                            CONF_CHIME_URL, description=_suggest(chime.get(CONF_CHIME_URL))
                        ): selector.TextSelector(),
                    }
                ),
                {"collapsed": True},
            ),
            vol.Required(SECTION_DELIVERY, default=dict(delivery)): section(
                vol.Schema(
                    {
                        vol.Required(
                            CONF_STAGGER_MS,
                            default=delivery.get(CONF_STAGGER_MS, DEFAULT_STAGGER_MS),
                        ): selector.NumberSelector(
                            selector.NumberSelectorConfig(
                                min=0,
                                max=2000,
                                step=50,
                                unit_of_measurement="ms",
                                mode=selector.NumberSelectorMode.BOX,
                            )
                        ),
                        vol.Required(
                            CONF_RETRY, default=delivery.get(CONF_RETRY, DEFAULT_RETRY)
                        ): selector.BooleanSelector(),
                        vol.Required(
                            CONF_RESTORE, default=delivery.get(CONF_RESTORE, DEFAULT_RESTORE)
                        ): selector.BooleanSelector(),
                        vol.Required(
                            CONF_URGENT_VOLUME,
                            default=delivery.get(CONF_URGENT_VOLUME, DEFAULT_URGENT_VOLUME),
                        ): _volume_slider(),
                    }
                ),
                {"collapsed": True},
            ),
            vol.Required(SECTION_QUIET, default=dict(quiet)): section(
                vol.Schema(
                    {
                        vol.Optional(
                            CONF_QUIET_START, description=_suggest(quiet.get(CONF_QUIET_START))
                        ): selector.TimeSelector(),
                        vol.Optional(
                            CONF_QUIET_END, description=_suggest(quiet.get(CONF_QUIET_END))
                        ): selector.TimeSelector(),
                        vol.Optional(
                            CONF_QUIET_VOLUME, description=_suggest(quiet.get(CONF_QUIET_VOLUME))
                        ): _volume_slider(),
                        vol.Optional(
                            CONF_QUIET_SKIP_AREAS,
                            description=_suggest(quiet.get(CONF_QUIET_SKIP_AREAS)),
                        ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                    }
                ),
                {"collapsed": True},
            ),
            vol.Required(SECTION_HOMEKIT, default=dict(homekit)): section(
                vol.Schema(
                    {
                        vol.Required(
                            CONF_HOMEKIT_ENABLED,
                            default=homekit.get(CONF_HOMEKIT_ENABLED, False),
                        ): selector.BooleanSelector(),
                        vol.Required(
                            CONF_HOMEKIT_PORT,
                            default=homekit.get(CONF_HOMEKIT_PORT, DEFAULT_HOMEKIT_PORT),
                        ): selector.NumberSelector(
                            selector.NumberSelectorConfig(
                                min=1024, max=65535, mode=selector.NumberSelectorMode.BOX
                            )
                        ),
                    }
                ),
                {"collapsed": True},
            ),
            vol.Required(SECTION_REPLY, default=dict(replies)): section(
                vol.Schema(
                    {
                        vol.Optional(
                            CONF_NOTIFY, description=_suggest(replies.get(CONF_NOTIFY))
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=_notify_targets(hass),
                                multiple=True,
                                custom_value=True,
                            )
                        ),
                        vol.Optional(
                            CONF_REPLY_PATH, description=_suggest(replies.get(CONF_REPLY_PATH))
                        ): selector.TextSelector(),
                        vol.Optional(
                            CONF_STT_ENGINE, description=_suggest(replies.get(CONF_STT_ENGINE))
                        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="stt")),
                    }
                ),
                {"collapsed": True},
            ),
            vol.Required("communication", default=dict(communication)): section(
                vol.Schema(
                    {
                        vol.Optional(
                            "prefer_music_assistant",
                            default=communication.get("prefer_music_assistant", []),
                        ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                        vol.Optional(
                            "aliases", default=communication.get("aliases", {})
                        ): selector.ObjectSelector(),
                        vol.Optional(
                            "playback_routes", default=communication.get("playback_routes", {})
                        ): selector.ObjectSelector(),
                        vol.Optional(
                            "playback_delays", default=communication.get("playback_delays", {})
                        ): selector.ObjectSelector(),
                        vol.Optional(
                            "preferences", default=communication.get("preferences", {})
                        ): selector.ObjectSelector(),
                        vol.Optional(
                            "adapters", default=communication.get("adapters", {})
                        ): selector.ObjectSelector(),
                        vol.Required(
                            "presence_max_age", default=communication.get("presence_max_age", 300)
                        ): selector.NumberSelector(
                            selector.NumberSelectorConfig(
                                min=10, max=86400, step=1, mode=selector.NumberSelectorMode.BOX
                            )
                        ),
                        vol.Required(
                            "presence_min_confidence",
                            default=communication.get("presence_min_confidence", 0),
                        ): _volume_slider(),
                        vol.Required(
                            "max_volume", default=communication.get("max_volume", 1)
                        ): _volume_slider(),
                        vol.Optional(
                            "agent_policies", default=communication.get("agent_policies", {})
                        ): selector.ObjectSelector(),
                        vol.Optional(
                            "household_profiles",
                            default=communication.get("household_profiles", {}),
                        ): selector.ObjectSelector(),
                        vol.Optional(
                            "speech_profiles", default=communication.get("speech_profiles", {})
                        ): selector.ObjectSelector(),
                        vol.Required(
                            "talk_back", default=communication.get("talk_back", False)
                        ): selector.BooleanSelector(),
                        vol.Optional(
                            "doorbell_entity",
                            description=_suggest(communication.get("doorbell_entity")),
                        ): selector.EntitySelector(
                            selector.EntitySelectorConfig(
                                domain=["binary_sensor", "event", "button"]
                            )
                        ),
                        vol.Optional(
                            "doorbell_camera",
                            description=_suggest(communication.get("doorbell_camera")),
                        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="camera")),
                        vol.Optional(
                            "doorbell_rooms", default=communication.get("doorbell_rooms", [])
                        ): selector.AreaSelector(selector.AreaSelectorConfig(multiple=True)),
                        vol.Required(
                            "voicemail", default=communication.get("voicemail", False)
                        ): selector.BooleanSelector(),
                        vol.Required(
                            "voicemail_retention",
                            default=communication.get("voicemail_retention", 86400),
                        ): selector.NumberSelector(
                            selector.NumberSelectorConfig(
                                min=300, max=604800, step=1, mode=selector.NumberSelectorMode.BOX
                            )
                        ),
                        vol.Optional(
                            "translation_entity",
                            description=_suggest(communication.get("translation_entity")),
                        ): selector.EntitySelector(selector.EntitySelectorConfig(domain="ai_task")),
                        vol.Required(
                            "audio_processing", default=communication.get("audio_processing", "off")
                        ): selector.SelectSelector(
                            selector.SelectSelectorConfig(
                                options=["off", "speech"], translation_key="audio_processing"
                            )
                        ),
                    }
                ),
                {"collapsed": True},
            ),
        }
    )


def _delay_seconds(value: Any) -> float:
    value = vol.Any(int, float)(value)
    if not 0 <= value <= 60:
        raise vol.Invalid("Delay must be between 0 and 60 seconds")
    return float(value)


def validate_communication(values: dict[str, Any]) -> None:
    """Validate freeform mappings before they enter the runtime configuration."""
    from .agent_policy import validate as validate_agent_policies

    if "agent_policies" in values:
        validate_agent_policies(values["agent_policies"])
    from .profiles import validate as validate_profiles

    if "household_profiles" in values:
        validate_profiles(values["household_profiles"])
    vol.Schema(
        {
            vol.Optional("agent_policies"): dict,
            vol.Optional("household_profiles"): dict,
            vol.Optional("speech_profiles"): {
                vol.In(("fast", "balanced", "quality")): vol.Schema(
                    {
                        vol.Required("engine"): vol.Match(r"^tts\.[a-z0-9_]+$"),
                        vol.Required("voice"): vol.All(str, vol.Length(min=1, max=128)),
                    }
                )
            },
            vol.Optional("prefer_music_assistant"): [str],
            vol.Optional("aliases"): {cv_entity: vol.All(str, vol.Length(min=1, max=80))},
            vol.Optional("playback_routes"): vol.All(
                {
                    cv_entity: vol.Schema(
                        {
                            vol.Required("controller"): cv_entity,
                            vol.Required("source"): vol.All(str, vol.Length(min=1, max=100)),
                        }
                    )
                },
                vol.Length(max=32),
            ),
            vol.Optional("playback_delays"): vol.All(
                {
                    cv_entity: vol.Schema(
                        {
                            vol.Optional("startup_delay"): _delay_seconds,
                            vol.Optional("restore_delay"): _delay_seconds,
                        }
                    )
                },
                vol.Length(max=32),
            ),
            vol.Optional("preferences"): {
                cv_person: vol.Schema(
                    {
                        vol.Optional("room"): str,
                        vol.Optional("language"): vol.Match(
                            r"^[a-zA-Z]{2,3}(?:-[a-zA-Z0-9]{2,8})?$"
                        ),
                    }
                )
            },
            vol.Optional("adapters"): {
                str: vol.Schema(
                    {
                        vol.Required("kind"): vol.In(("sip", "doorbell")),
                        vol.Required("script"): vol.Match(r"^script\.[a-z0-9_]+$"),
                    }
                )
            },
            vol.Optional("presence_max_age"): vol.All(
                vol.Coerce(int), vol.Range(min=10, max=86400)
            ),
            vol.Optional("presence_min_confidence"): vol.All(
                vol.Coerce(float), vol.Range(min=0, max=1)
            ),
            vol.Optional("max_volume"): vol.All(vol.Coerce(float), vol.Range(min=0, max=1)),
            vol.Optional("voicemail"): bool,
            vol.Optional("talk_back"): bool,
            vol.Optional("doorbell_entity"): vol.Match(
                r"^(binary_sensor|event|button)\.[a-z0-9_]+$"
            ),
            vol.Optional("doorbell_camera"): vol.Match(r"^camera\.[a-z0-9_]+$"),
            vol.Optional("doorbell_rooms"): [str],
            vol.Optional("voicemail_retention"): vol.All(
                vol.Coerce(int), vol.Range(min=300, max=604800)
            ),
            vol.Optional("translation_entity"): vol.Match(r"^ai_task\.[a-z0-9_]+$"),
            vol.Optional("audio_processing"): vol.In(("off", "speech")),
        }
    )(values)


def cv_entity(value: str) -> str:
    return vol.Match(r"^media_player\.[a-z0-9_]+$")(value)


def cv_person(value: str) -> str:
    return vol.Match(r"^person\.[a-z0-9_]+$")(value)


class RoomCallConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        # Opening HA's normal config flow bootstraps the API and setup UI, even
        # before a RoomCall entry exists. Installation stays with HA/HACS.
        await async_setup_component(self.hass, DOMAIN, {})
        from .discovery import async_refresh

        await async_refresh(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_SPEAKERS):
                errors[CONF_SPEAKERS] = "no_speakers"
            else:
                return self.async_create_entry(title="RoomCall", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(_main_fields(self.hass, user_input or {})),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> RoomCallOptionsFlow:
        return RoomCallOptionsFlow()

    @classmethod
    @callback
    def async_get_supported_subentry_types(
        cls, config_entry: ConfigEntry
    ) -> dict[str, type[ConfigSubentryFlow]]:
        return {
            SUBENTRY_SCHEDULED: ScheduledAnnouncementFlow,
            SUBENTRY_TRIGGERED: TriggeredAnnouncementFlow,
        }


class RoomCallOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        from .discovery import async_refresh

        await async_refresh(self.hass)
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_SPEAKERS):
                errors[CONF_SPEAKERS] = "no_speakers"
            else:
                try:
                    validate_communication(user_input.get("communication") or {})
                except vol.Invalid:
                    errors["base"] = "invalid_settings"
                else:
                    return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options, **(user_input or {})}
        return self.async_show_form(
            step_id="init", data_schema=_options_schema(self.hass, current), errors=errors
        )


def _target_fields(defaults: dict[str, Any]) -> dict[Any, Any]:
    return {
        vol.Required(
            CONF_MESSAGE, default=defaults.get(CONF_MESSAGE, "")
        ): selector.TemplateSelector(),
        vol.Optional(CONF_ROOMS, default=defaults.get(CONF_ROOMS, [])): selector.AreaSelector(
            selector.AreaSelectorConfig(multiple=True)
        ),
        vol.Optional(
            CONF_TARGET_SPEAKERS, default=defaults.get(CONF_TARGET_SPEAKERS, [])
        ): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="media_player", multiple=True)
        ),
        vol.Optional(CONF_PEOPLE, default=defaults.get(CONF_PEOPLE, [])): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="person", multiple=True)
        ),
        vol.Required(
            CONF_PRIORITY, default=defaults.get(CONF_PRIORITY, PRIORITY_NORMAL)
        ): selector.SelectSelector(
            selector.SelectSelectorConfig(options=list(PRIORITIES), translation_key="priority")
        ),
        # Optional task: what someone confirms they did, so a dismissed alert isn't "done".
        vol.Optional(
            CONF_TASK, description={"suggested_value": defaults.get(CONF_TASK)}
        ): selector.TextSelector(),
        vol.Required(
            CONF_REMIND_EVERY, default=defaults.get(CONF_REMIND_EVERY, DEFAULT_REMIND_EVERY)
        ): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=1440, unit_of_measurement="min", mode=selector.NumberSelectorMode.BOX
            )
        ),
    }


class _AnnouncementFlow(ConfigSubentryFlow):
    """Shared add/reconfigure steps for announcements."""

    def _schema(self, defaults: dict[str, Any]) -> vol.Schema:
        raise NotImplementedError

    def _validate(self, data: dict[str, Any]) -> dict[str, str]:
        errors: dict[str, str] = {}
        if not str(data.get(CONF_MESSAGE, "")).strip():
            errors[CONF_MESSAGE] = "no_message"
        return errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> SubentryFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate(user_input)
            if not errors:
                title = user_input.pop("name")
                return self.async_create_entry(title=title, data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=self._schema(user_input or {}), errors=errors
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> SubentryFlowResult:
        subentry = self._get_reconfigure_subentry()
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = self._validate(user_input)
            if not errors:
                title = user_input.pop("name")
                return self.async_update_and_abort(
                    self._get_entry(), subentry, title=title, data=user_input
                )
        defaults = {"name": subentry.title, **subentry.data, **(user_input or {})}
        return self.async_show_form(
            step_id="reconfigure", data_schema=self._schema(defaults), errors=errors
        )


class ScheduledAnnouncementFlow(_AnnouncementFlow):
    """A message at a time of day, on chosen weekdays."""

    def _schema(self, defaults: dict[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("name", default=defaults.get("name", "")): selector.TextSelector(),
                vol.Required(CONF_TIME, default=defaults.get(CONF_TIME, "08:00:00")): (
                    selector.TimeSelector()
                ),
                vol.Required(CONF_DAYS, default=defaults.get(CONF_DAYS, list(DAYS))): (
                    selector.SelectSelector(
                        selector.SelectSelectorConfig(
                            options=list(DAYS), multiple=True, translation_key="days"
                        )
                    )
                ),
                **_target_fields(defaults),
            }
        )

    def _validate(self, data: dict[str, Any]) -> dict[str, str]:
        errors = super()._validate(data)
        if not data.get(CONF_DAYS):
            errors[CONF_DAYS] = "no_days"
        return errors


class TriggeredAnnouncementFlow(_AnnouncementFlow):
    """A message when an entity changes to a state (a switch, a door, a sensor...)."""

    def _schema(self, defaults: dict[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Required("name", default=defaults.get("name", "")): selector.TextSelector(),
                vol.Required(CONF_ENTITY, default=defaults.get(CONF_ENTITY, "")): (
                    selector.EntitySelector()
                ),
                vol.Optional(
                    CONF_TO_STATE, description={"suggested_value": defaults.get(CONF_TO_STATE)}
                ): selector.TextSelector(),
                vol.Optional(
                    CONF_FOR, description={"suggested_value": defaults.get(CONF_FOR)}
                ): selector.DurationSelector(),
                vol.Required(
                    CONF_COOLDOWN, default=defaults.get(CONF_COOLDOWN, DEFAULT_COOLDOWN)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0,
                        max=86400,
                        unit_of_measurement="s",
                        mode=selector.NumberSelectorMode.BOX,
                    )
                ),
                **_target_fields(defaults),
            }
        )
