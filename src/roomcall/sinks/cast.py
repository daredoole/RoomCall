"""Chromecast / Google Cast backend using pychromecast."""

from __future__ import annotations

import asyncio
from typing import Any

from ..models import Speaker
from .base import SinkUnavailable


class CastSink:
    async def play(self, speaker: Speaker, url: str, volume: float | None) -> None:
        await asyncio.to_thread(self._play_blocking, speaker, url, volume)

    @staticmethod
    def _play_blocking(speaker: Speaker, url: str, volume: float | None) -> None:
        try:
            import pychromecast
        except ImportError as err:
            raise SinkUnavailable("install roomcall[cast] to talk to Cast speakers") from err

        cast: Any = pychromecast.get_chromecast_from_host(
            (speaker.host, speaker.port, None, speaker.name, speaker.name)
        )
        cast.wait(timeout=5)
        try:
            if volume is not None:
                cast.set_volume(volume)
            controller = cast.media_controller
            controller.play_media(url, "audio/mpeg", title="RoomCall")
            controller.block_until_active(timeout=5)
        finally:
            cast.disconnect(blocking=False)
