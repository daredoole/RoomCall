"""Fan one clip out to every speaker in the target, in parallel."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping, Sequence

from .models import BroadcastResult, DeliveryResult, Speaker, SpeakerKind
from .sinks.base import Sink

_LOGGER = logging.getLogger(__name__)


async def _deliver(
    sink: Sink | None, speaker: Speaker, url: str, volume: float | None, deadline_s: float
) -> DeliveryResult:
    started = time.perf_counter()
    if sink is None:
        return DeliveryResult(speaker, False, 0.0, f"no backend for {speaker.kind.value}")
    try:
        async with asyncio.timeout(deadline_s):
            await sink.play(speaker, url, volume)
    except Exception as err:
        _LOGGER.warning("Delivery to %s failed: %s", speaker.name, err)
        elapsed = (time.perf_counter() - started) * 1000
        return DeliveryResult(speaker, False, elapsed, str(err) or type(err).__name__)
    return DeliveryResult(speaker, True, (time.perf_counter() - started) * 1000)


async def broadcast(
    target: str,
    speakers: Sequence[Speaker],
    url: str,
    sinks: Mapping[SpeakerKind, Sink],
    *,
    volume: float | None = None,
    deadline_s: float = 90.0,
) -> BroadcastResult:
    results = await asyncio.gather(
        *(_deliver(sinks.get(s.kind), s, url, volume, deadline_s) for s in speakers)
    )
    return BroadcastResult(target=target, clip_url=url, results=list(results))
