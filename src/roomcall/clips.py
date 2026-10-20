"""In-memory store for recorded clips that speakers fetch over HTTP."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Clip:
    id: str
    data: bytes
    content_type: str
    extension: str
    created: float


class ClipStore:
    """Keeps recent clips for ``ttl`` seconds so every speaker has time to fetch them."""

    def __init__(self, ttl: float = 120.0, max_clips: int = 50) -> None:
        self._ttl = ttl
        self._max = max_clips
        self._clips: dict[str, Clip] = {}

    def add(self, data: bytes, content_type: str, extension: str) -> Clip:
        self.prune()
        clip = Clip(secrets.token_urlsafe(12), data, content_type, extension, time.monotonic())
        self._clips[clip.id] = clip
        while len(self._clips) > self._max:
            self._clips.pop(next(iter(self._clips)))
        return clip

    def get(self, clip_id: str) -> Clip | None:
        self.prune()
        return self._clips.get(clip_id)

    def prune(self) -> None:
        cutoff = time.monotonic() - self._ttl
        for clip_id in [k for k, c in self._clips.items() if c.created < cutoff]:
            del self._clips[clip_id]

    def __len__(self) -> int:
        return len(self._clips)
