"""AirPlay backend using pyatv's streaming support (HomePod, AirPlay receivers, Apple TV)."""

from __future__ import annotations

import asyncio
from typing import Any

from ..models import Speaker
from .base import SinkUnavailable


class AirPlaySink:
    async def play(self, speaker: Speaker, url: str, volume: float | None) -> None:
        try:
            import pyatv
        except ImportError as err:
            raise SinkUnavailable("install roomcall[airplay] to talk to AirPlay speakers") from err

        loop = asyncio.get_running_loop()
        configs: list[Any] = await pyatv.scan(loop, hosts=[speaker.host], timeout=3)
        if not configs:
            raise ConnectionError(f"AirPlay device {speaker.host} did not answer")
        atv: Any = await pyatv.connect(configs[0], loop)
        try:
            if volume is not None:
                await atv.audio.set_volume(volume * 100)
            await atv.stream.stream_file(url)
        finally:
            atv.close()
