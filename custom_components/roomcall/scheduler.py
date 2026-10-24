"""Bounded endpoint leases retained until playback and restoration finish."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant

from .capabilities import physical_key

if TYPE_CHECKING:
    from .restore import Guard


async def wait_for_playback(
    hass: HomeAssistant,
    entity_id: str,
    seconds: float | None,
    until: Callable[[], Awaitable[None]] | None,
    audio_seconds: Callable[[], float] | None = None,
) -> None:
    from .restore import LIVE_TAIL_SECONDS, wait_until_done

    if until is not None:
        await until()
        await asyncio.sleep(LIVE_TAIL_SECONDS)
        if audio_seconds is not None:
            seconds = audio_seconds()
    await wait_until_done(hass, entity_id, seconds)


class Scheduler:
    def __init__(self, hass: HomeAssistant, aliases: dict[str, str]) -> None:
        self.hass = hass
        self.aliases = aliases
        self.locks: dict[str, asyncio.Lock] = {}
        self.pending = 0
        self.tasks: set[asyncio.Task] = set()

    async def acquire(self, entity_id: str, deadline: float | None) -> asyncio.Lock:
        if self.pending >= 128:
            raise RuntimeError("queue_full")
        key = physical_key(self.hass, entity_id, self.aliases)
        lock = self.locks.setdefault(key, asyncio.Lock())
        self.pending += 1
        try:
            async with asyncio.timeout_at(deadline):
                await lock.acquire()
        finally:
            self.pending -= 1
        return lock

    def finish(
        self,
        lock: asyncio.Lock,
        entity_id: str,
        guard: Guard | None,
        seconds: float | None,
        until: Callable[[], Awaitable[None]] | None,
        *,
        audio_seconds: Callable[[], float] | None = None,
    ) -> None:
        async def release() -> None:
            try:
                if guard is not None:
                    await guard.finish(seconds, until, audio_seconds=audio_seconds)
                else:
                    if audio_seconds is None:
                        await wait_for_playback(self.hass, entity_id, seconds, until)
                    else:
                        await wait_for_playback(self.hass, entity_id, seconds, until, audio_seconds)
            finally:
                lock.release()

        task = self.hass.async_create_background_task(release(), f"roomcall lease {entity_id}")
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
