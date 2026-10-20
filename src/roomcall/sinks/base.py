"""Backend interface."""

from __future__ import annotations

from typing import Protocol

from ..models import Speaker


class SinkUnavailable(RuntimeError):
    """The optional library for this speaker type is not installed."""


class Sink(Protocol):
    async def play(self, speaker: Speaker, url: str, volume: float | None) -> None:
        """Play the clip at ``url`` on ``speaker``; return once playback has been accepted."""
