"""Named household defaults without changing global playback settings."""

from __future__ import annotations

from typing import Any

from .core import expand_group_targets
from .errors import Problem
from .schema import vol

SPEECH = vol.Schema(
    {
        vol.Optional("engine"): vol.Match(r"^tts\.[a-z0-9_]+$"),
        vol.Optional("voice"): vol.All(str, vol.Length(min=1, max=128)),
        vol.Optional("chime"): vol.In(("chirp", "ding", "double", "rising", "doorbell", "off")),
    }
)


def validate(profiles: dict) -> dict:
    from .communication import MESSAGE

    if len(profiles) > 32:
        raise vol.Invalid("Too many profiles")
    result = {}
    for key, item in profiles.items():
        vol.Match(r"^[a-z0-9_]{1,40}$")(key)
        item = vol.Schema(
            {
                vol.Required("name"): vol.All(str, vol.Length(min=1, max=80)),
                vol.Required("defaults"): dict,
                vol.Optional("speech", default={}): SPEECH,
            }
        )(item)
        if "profile" in item["defaults"]:
            raise vol.Invalid("Recursive profile")
        normalized = vol.Schema(MESSAGE)(item["defaults"])
        item["defaults"] = expand_group_targets({key: normalized[key] for key in item["defaults"]})
        if not any(item["defaults"].get(k) for k in ("areas", "speakers", "people", "everywhere")):
            raise vol.Invalid("A profile needs explicit destinations")
        result[key] = item
    return result


def expand(data, values: dict[str, Any]) -> dict[str, Any]:
    profile = data.household_profiles.get(values.get("profile"))
    if profile is None:
        raise Problem("profile_missing")
    defaults = dict(profile["defaults"])
    if any(key in values for key in ("areas", "speakers", "people", "everywhere")):
        for key in ("areas", "speakers", "people", "everywhere"):
            defaults.pop(key, None)
    return {**defaults, **values}


async def available(hass, data, context, assistant):
    from .communication import permitted
    from .core import resolve

    result = []
    for key, profile in data.household_profiles.items():
        defaults = profile["defaults"]
        endpoints = resolve(
            hass, data.speakers, [*defaults.get("areas", []), *defaults.get("speakers", [])]
        )
        entities = [*endpoints, *defaults.get("people", [])]
        if entities and all([await permitted(hass, e, context, assistant) for e in entities]):
            result.append(
                {"id": key, "name": profile["name"], "message": defaults.get("message", "")}
            )
    return result
