"""Explicit per-account RoomCall limits, additional to HA permissions/exposure."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from homeassistant.util import dt as dt_util

from .core import speaker_area
from .errors import Problem
from .schema import vol

OPERATIONS = [
    "discover",
    "diagnose",
    "preview",
    "send",
    "speech",
    "ask",
    "status",
    "cancel",
    "acknowledge",
    "tasks",
    "voicemail",
    "policy",
]
CLOCK = vol.Match(r"^(?:[01]\d|2[0-3]):[0-5]\d$")
GRANT = vol.Schema(
    {
        vol.Optional("areas", default=[]): [str],
        vol.Optional("entities", default=[]): [vol.Match(r"^[a-z_]+\.[a-z0-9_]+$")],
        vol.Optional("operations", default=["discover", "preview", "send", "status", "policy"]): [
            vol.In(OPERATIONS)
        ],
        vol.Optional("urgent", default=False): bool,
        vol.Optional("max_volume", default=0.4): vol.All(
            vol.Coerce(float), vol.Range(min=0, max=1)
        ),
        vol.Optional("per_minute", default=10): vol.All(int, vol.Range(min=1, max=60)),
        vol.Optional("start"): CLOCK,
        vol.Optional("end"): CLOCK,
    }
)


def validate(values):
    if len(values) > 32:
        raise vol.Invalid("Too many policies")
    result = {}
    for key, value in values.items():
        vol.All(str, vol.Length(min=1, max=80))(key)
        grant = GRANT(value)
        if ("start" in grant) != ("end" in grant):
            raise vol.Invalid("Both start and end are required")
        result[key] = grant
    return result


class AgentPolicies:
    def __init__(self, grants):
        self.grants = validate(grants)
        self.recent = defaultdict(deque)

    def entity(self, hass, owner, entity_id):
        grant = self.grants.get(owner)
        return (
            grant is None
            or entity_id in grant["entities"]
            or speaker_area(hass, entity_id) in grant["areas"]
        )

    def authorize(self, owner, operation):
        grant = self.grants.get(owner)
        if grant is None or operation in {"catalog", "policy"}:
            return
        if operation not in grant["operations"]:
            raise Problem("agent_policy_denied")

    def playback(self, hass, owner, entities, priority, volume):
        grant = self.grants.get(owner)
        if grant is None:
            return 1.0
        now = dt_util.now().strftime("%H:%M")
        start, end = grant.get("start"), grant.get("end")
        if start is not None:
            allowed = start <= now < end if start < end else now >= start or now < end
            if not allowed:
                raise Problem("agent_policy_denied")
        if (
            (priority == "urgent" and not grant["urgent"])
            or (volume is not None and volume > grant["max_volume"])
            or not all(self.entity(hass, owner, e) for e in entities)
        ):
            raise Problem("agent_policy_denied")
        return grant["max_volume"]

    def consume(self, owner):
        grant = self.grants.get(owner)
        if grant is None:
            return
        recent = self.recent[owner]
        now = time.monotonic()
        while recent and now - recent[0] >= 60:
            recent.popleft()
        if len(recent) >= grant["per_minute"]:
            raise Problem("agent_rate_limited", retryable=True)
        recent.append(now)

    def summary(self, owner):
        grant = self.grants.get(owner)
        return {
            "restricted": grant is not None,
            "limits": grant,
            "rate_window_seconds": 60,
            "rate_persistence": "runtime",
            "scope": "roomcall_only",
        }
