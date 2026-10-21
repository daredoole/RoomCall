"""Use Home Assistant's current schema library while retaining older HA support."""

from homeassistant.helpers import config_validation as cv

if type(cv.config_entry_only_config_schema("roomcall")).__module__.startswith("probatio"):
    import probatio as vol
else:
    import voluptuous as vol  # noqa: F401
