"""Speaker backends. Each backend plays a clip URL on one kind of speaker."""

from __future__ import annotations

from ..models import SpeakerKind
from .base import Sink, SinkUnavailable


def default_sinks() -> dict[SpeakerKind, Sink]:
    """Instantiate the built-in backends; missing optional libraries surface at play time."""
    from .airplay import AirPlaySink
    from .cast import CastSink
    from .sonos import SonosSink

    return {
        SpeakerKind.SONOS: SonosSink(),
        SpeakerKind.AIRPLAY: AirPlaySink(),
        SpeakerKind.CAST: CastSink(),
    }


__all__ = ["Sink", "SinkUnavailable", "default_sinks"]
