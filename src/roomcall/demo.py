"""Demo mode: fake speakers and a backend that pretends to play, for trying the UI anywhere."""

from __future__ import annotations

import asyncio
import random

from .models import Speaker, SpeakerKind

DEMO_ROOMS: dict[str, list[str]] = {
    "Kitchen": ["Kitchen Speaker", "Kitchen Hub"],
    "Living Room": ["Living Room Sonos"],
    "Bedroom": ["Bedroom HomePod"],
    "Garage": ["Garage Speaker"],
}

DEMO_SPEAKERS: list[Speaker] = [
    Speaker("sonos:demo1", "Kitchen Speaker", SpeakerKind.SONOS, "10.9.0.11", 1400, "Era 100"),
    Speaker("cast:demo2", "Kitchen Hub", SpeakerKind.CAST, "10.9.0.12", 8009, "Nest Hub"),
    Speaker("sonos:demo3", "Living Room Sonos", SpeakerKind.SONOS, "10.9.0.13", 1400, "Arc"),
    Speaker("airplay:demo4", "Bedroom HomePod", SpeakerKind.AIRPLAY, "10.9.0.14", 7000, "HomePod"),
    Speaker("airplay:demo5", "Garage Speaker", SpeakerKind.AIRPLAY, "10.9.0.15", 7000, "AirPort"),
]


async def demo_discover(duration: float) -> list[Speaker]:
    return list(DEMO_SPEAKERS)


class DemoSink:
    async def play(self, speaker: Speaker, url: str, volume: float | None) -> None:
        await asyncio.sleep(random.uniform(0.3, 1.2))
