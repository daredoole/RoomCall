"""Recently available media players for setup, without treating HA restarts as sightings."""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from functools import partial

from homeassistant.const import EVENT_HOMEASSISTANT_STOP, EVENT_STATE_CHANGED
from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

_KEY = "roomcall_discovery"
WINDOW = timedelta(hours=24)
_LOGGER = logging.getLogger(__name__)


def available(state: State | None) -> bool:
    return bool(
        state
        and state.state not in {"unavailable", "unknown"}
        and not state.attributes.get("restored")
    )


class RecentPlayers:
    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass
        self.store = Store(hass, 1, "roomcall.discovery")
        self.seen: dict[str, dict] = {}
        self.loaded = False
        self.checked: set[str] = set()
        self.lock = asyncio.Lock()
        unsubscribe = hass.bus.async_listen(EVENT_STATE_CHANGED, self.changed)
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, lambda _: unsubscribe())

    def observe(self, state: State, at: float) -> None:
        if available(state):
            self.seen[state.entity_id] = {
                "at": at,
                "features": state.attributes.get("supported_features", 0),
            }

    @callback
    def changed(self, event) -> None:
        entity_id = event.data["entity_id"]
        if not entity_id.startswith("media_player."):
            return
        old, new = event.data.get("old_state"), event.data.get("new_state")
        # A transition away from an available state proves it was available until now.
        if available(new) or available(old):
            self.observe(new if available(new) else old, dt_util.utcnow().timestamp())
            self.store.async_delay_save(lambda: self.seen, 5)

    def recent(self, entity_id: str) -> bool:
        state = self.hass.states.get(entity_id)
        entry = er.async_get(self.hass).async_get(entity_id)
        return bool(
            state
            and not (entry and entry.disabled_by)
            and (
                available(state)
                or self.seen.get(entity_id, {}).get("at", 0)
                >= (dt_util.utcnow() - WINDOW).timestamp()
            )
        )

    def audio_capable(self, entity_id: str) -> bool:
        from .capabilities import can_play

        entry = er.async_get(self.hass).async_get(entity_id)
        if entry and entry.platform in {"androidtv_remote", "denonavr"}:
            return False
        return can_play(self.hass, entity_id) or bool(
            self.seen.get(entity_id, {}).get("features", 0) & 512
        )

    async def refresh(self) -> None:
        async with self.lock:
            if not self.loaded:
                stored = await self.store.async_load() or {}
                self.seen = {**stored, **self.seen}
                self.loaded = True
            now = dt_util.utcnow()
            cutoff = (now - WINDOW).timestamp()
            self.seen = {
                entity: value for entity, value in self.seen.items() if value.get("at", 0) >= cutoff
            }
            missing = []
            for state in self.hass.states.async_all("media_player"):
                if available(state):
                    self.observe(state, now.timestamp())
                elif state.entity_id not in self.checked and not self.recent(state.entity_id):
                    missing.append(state.entity_id)
            if missing and "recorder_instance" in self.hass.data:
                from homeassistant.components.recorder.history import get_significant_states
                from homeassistant.helpers.recorder import get_instance

                try:
                    records = await get_instance(self.hass).async_add_executor_job(
                        partial(
                            get_significant_states,
                            self.hass,
                            now - WINDOW,
                            now,
                            missing,
                            significant_changes_only=True,
                        )
                    )
                except Exception:
                    _LOGGER.warning("Could not read recent media-player history", exc_info=True)
                else:
                    self.checked.update(missing)
                    for states in records.values():
                        previous = None
                        for state in states:
                            if available(state):
                                self.observe(state, state.last_updated.timestamp())
                            elif available(previous):
                                self.observe(previous, state.last_updated.timestamp())
                            previous = state
            self.store.async_delay_save(lambda: self.seen, 5)


def players(hass: HomeAssistant) -> RecentPlayers:
    if _KEY not in hass.data:
        hass.data[_KEY] = RecentPlayers(hass)
    return hass.data[_KEY]


async def async_refresh(hass: HomeAssistant) -> None:
    await players(hass).refresh()


def speaker_choices(hass: HomeAssistant, saved: list[str]) -> list[str]:
    """Saved values remain editable; new choices must be recently available audio players."""
    recent = players(hass)
    return sorted(
        set(saved)
        | {
            state.entity_id
            for state in hass.states.async_all("media_player")
            if recent.recent(state.entity_id) and recent.audio_capable(state.entity_id)
        }
    )
