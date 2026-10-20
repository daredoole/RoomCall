"""Map speakers to rooms and resolve a broadcast target to concrete speakers."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field

from .models import Speaker

ALL = "all"


def _norm(value: str) -> str:
    return " ".join(value.lower().replace("_", " ").replace("-", " ").split())


@dataclass(slots=True)
class RoomMap:
    """Room name -> speaker names (or ids). Unassigned speakers fall back to their own name."""

    rooms: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def from_config(cls, data: Mapping[str, Iterable[str]] | None) -> RoomMap:
        return cls({room: list(members) for room, members in (data or {}).items()})

    def room_for(self, speaker: Speaker) -> str:
        keys = {_norm(speaker.name), _norm(speaker.id)}
        for room, members in self.rooms.items():
            if keys & {_norm(m) for m in members}:
                return room
        return speaker.name

    def assign(self, speakers: Iterable[Speaker]) -> list[Speaker]:
        return [s.with_room(self.room_for(s)) for s in speakers]

    def room_names(self, speakers: Iterable[Speaker]) -> list[str]:
        return sorted({s.room or s.name for s in self.assign(speakers)}, key=str.lower)

    def resolve(self, target: str, speakers: Iterable[Speaker]) -> list[Speaker]:
        """Return the speakers for ``target``: ``all``, a room name, or a speaker name/id."""
        assigned = self.assign(speakers)
        if _norm(target) == ALL:
            return assigned
        wanted = _norm(target)
        by_room = [s for s in assigned if s.room and _norm(s.room) == wanted]
        if by_room:
            return by_room
        return [s for s in assigned if wanted in (_norm(s.name), _norm(s.id))]
