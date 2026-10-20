"""Core data types shared by discovery, routing and the speaker backends."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class SpeakerKind(StrEnum):
    SONOS = "sonos"
    AIRPLAY = "airplay"
    CAST = "cast"


# When one device advertises several protocols, the lower number wins.
# Native Sonos control supports announcement ducking; AirPlay on a Sonos does not.
KIND_PRIORITY: dict[SpeakerKind, int] = {
    SpeakerKind.SONOS: 0,
    SpeakerKind.CAST: 1,
    SpeakerKind.AIRPLAY: 2,
}


@dataclass(frozen=True, slots=True)
class Speaker:
    """A network speaker that RoomCall can send audio to."""

    id: str
    name: str
    kind: SpeakerKind
    host: str
    port: int
    model: str | None = None
    room: str | None = None

    def with_room(self, room: str | None) -> Speaker:
        return Speaker(self.id, self.name, self.kind, self.host, self.port, self.model, room)


@dataclass(slots=True)
class DeliveryResult:
    """Outcome of sending one clip to one speaker."""

    speaker: Speaker
    ok: bool
    elapsed_ms: float
    error: str | None = None


@dataclass(slots=True)
class BroadcastResult:
    """Outcome of one push-to-talk message across all targeted speakers."""

    target: str
    clip_url: str
    results: list[DeliveryResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.results) and all(r.ok for r in self.results)
