from __future__ import annotations

import pytest

from roomcall.models import Speaker, SpeakerKind


class FakeSink:
    """Records plays; fails for any host listed in ``fail_hosts``."""

    def __init__(self, fail_hosts: set[str] | None = None) -> None:
        self.plays: list[tuple[str, str, float | None]] = []
        self.fail_hosts = fail_hosts or set()

    async def play(self, speaker: Speaker, url: str, volume: float | None) -> None:
        if speaker.host in self.fail_hosts:
            raise ConnectionError("speaker offline")
        self.plays.append((speaker.name, url, volume))


@pytest.fixture
def speakers() -> list[Speaker]:
    return [
        Speaker("sonos:1", "Basement", SpeakerKind.SONOS, "10.0.0.10", 1400, "One"),
        Speaker("sonos:2", "Office", SpeakerKind.SONOS, "10.0.0.11", 1400, "Era 100"),
        Speaker("airplay:3", "Garage HomePod", SpeakerKind.AIRPLAY, "10.0.0.12", 7000, "HomePod"),
        Speaker("cast:4", "Kitchen Hub", SpeakerKind.CAST, "10.0.0.13", 8009, "Nest Hub"),
    ]
