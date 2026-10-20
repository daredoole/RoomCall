"""Config and options flow: pick which media players act as intercom speakers."""

from __future__ import annotations

from typing import Any

import voluptuous as vol
from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import selector

from .const import (
    CONF_CHIME,
    CONF_GAIN_DB,
    CONF_SPEAKERS,
    CONF_TTS_ENGINE,
    CONF_VOLUME,
    DEFAULT_CHIME,
    DOMAIN,
)

# Music Assistant mirrors physical speakers that already have a native entity; skipping it by
# default avoids playing every message twice.
_SKIP_PLATFORMS_BY_DEFAULT = {"music_assistant", "dlna_dmr", "mobile_app"}


def suggested_speakers(hass: HomeAssistant) -> list[str]:
    registry = er.async_get(hass)
    out = []
    for state in hass.states.async_all("media_player"):
        entry = registry.async_get(state.entity_id)
        if entry and (entry.disabled_by or entry.platform in _SKIP_PLATFORMS_BY_DEFAULT):
            continue
        features = state.attributes.get("supported_features", 0)
        if features & MediaPlayerEntityFeature.MEDIA_ANNOUNCE:
            out.append(state.entity_id)
    return sorted(out)


def _schema(hass: HomeAssistant, defaults: dict[str, Any]) -> vol.Schema:
    return vol.Schema(
        {
            vol.Required(
                CONF_SPEAKERS, default=defaults.get(CONF_SPEAKERS, suggested_speakers(hass))
            ): selector.EntitySelector(
                selector.EntitySelectorConfig(domain="media_player", multiple=True)
            ),
            vol.Optional(
                CONF_VOLUME,
                description={"suggested_value": defaults.get(CONF_VOLUME)},
            ): selector.NumberSelector(
                selector.NumberSelectorConfig(min=0, max=1, step=0.05, mode="slider")
            ),
            vol.Optional(CONF_CHIME, default=defaults.get(CONF_CHIME, DEFAULT_CHIME)): (
                selector.BooleanSelector()
            ),
            vol.Optional(CONF_GAIN_DB, default=defaults.get(CONF_GAIN_DB, 0)): (
                selector.NumberSelector(
                    selector.NumberSelectorConfig(min=-10, max=15, step=1, mode="box")
                )
            ),
            vol.Optional(
                CONF_TTS_ENGINE,
                description={"suggested_value": defaults.get(CONF_TTS_ENGINE)},
            ): selector.EntitySelector(selector.EntitySelectorConfig(domain="tts")),
        }
    )


class RoomCallConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_SPEAKERS):
                errors[CONF_SPEAKERS] = "no_speakers"
            else:
                return self.async_create_entry(title="RoomCall", data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=_schema(self.hass, user_input or {}), errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> RoomCallOptionsFlow:
        return RoomCallOptionsFlow()


class RoomCallOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        current = {**self.config_entry.data, **self.config_entry.options}
        return self.async_show_form(step_id="init", data_schema=_schema(self.hass, current))
