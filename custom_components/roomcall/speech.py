"""Bounded, owner-scoped prepared speech; no disk retention or speaker playback."""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable

from .errors import Problem


class SpeechCache:
    def __init__(self) -> None:
        self.items: OrderedDict[str, tuple[float, bytes, str]] = OrderedDict()
        self.locks: dict[str, tuple[asyncio.Lock, int]] = {}
        self.generation = 0

    def clear(self) -> None:
        self.items.clear()
        self.generation += 1

    async def prepare(
        self, key: str, build: Callable[[], Awaitable[tuple[bytes, str]]]
    ) -> tuple[bytes, str, bool]:
        if key not in self.locks and len(self.locks) >= 32:
            raise Problem("queue_full", retryable=True)
        lock, users = self.locks.get(key, (asyncio.Lock(), 0))
        self.locks[key] = (lock, users + 1)
        try:
            async with lock:
                now = time.monotonic()
                for expired in [k for k, value in self.items.items() if now - value[0] >= 600]:
                    self.items.pop(expired)
                if cached := self.items.get(key):
                    self.items.move_to_end(key)
                    return cached[1], cached[2], True
                generation = self.generation
                audio, content_type = await build()
                # A clear while a provider is working must not repopulate old audio.
                if generation == self.generation and len(audio) <= 4 * 1024 * 1024:
                    self.items[key] = (time.monotonic(), audio, content_type)
                    while (
                        len(self.items) > 32
                        or sum(len(v[1]) for v in self.items.values()) > 16 * 1024 * 1024
                    ):
                        self.items.popitem(last=False)
                return audio, content_type, False
        finally:
            users = self.locks[key][1] - 1
            if users:
                self.locks[key] = (lock, users)
            else:
                self.locks.pop(key)
