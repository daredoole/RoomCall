"""Sonos backend using SoCo: snapshot, play the clip, then restore what was playing."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from ..models import Speaker
from .base import SinkUnavailable

_MAX_CLIP_SECONDS = 60.0


class SonosSink:
    async def play(self, speaker: Speaker, url: str, volume: float | None) -> None:
        await asyncio.to_thread(self._play_blocking, speaker, url, volume)

    @staticmethod
    def _play_blocking(speaker: Speaker, url: str, volume: float | None) -> None:
        try:
            import soco
            from soco.snapshot import Snapshot
        except ImportError as err:
            raise SinkUnavailable("install roomcall[sonos] to talk to Sonos speakers") from err

        device: Any = soco.SoCo(speaker.host)
        snapshot = Snapshot(device)
        snapshot.snapshot()
        try:
            if volume is not None:
                device.volume = round(volume * 100)
            device.play_uri(url, title="RoomCall")
            deadline = time.monotonic() + _MAX_CLIP_SECONDS
            time.sleep(0.5)
            while time.monotonic() < deadline:
                state = device.get_current_transport_info().get("current_transport_state")
                if state not in ("PLAYING", "TRANSITIONING"):
                    break
                time.sleep(0.25)
        finally:
            snapshot.restore(fade=True)
