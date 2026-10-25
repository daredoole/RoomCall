"""Bounded local observations. No transcripts, remote telemetry or inferred audibility."""

from __future__ import annotations

import secrets
import time
from collections import Counter, OrderedDict, defaultdict, deque
from datetime import timedelta
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.http.auth import async_sign_path
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_call_later, async_track_state_change_event

from .const import CLIP_TTL_SECONDS
from .core import CallRecord


class Metrics:
    def __init__(self) -> None:
        self.counters: Counter[str] = Counter()
        self.samples: dict[str, deque[float]] = defaultdict(lambda: deque(maxlen=256))
        self.started = time.monotonic()

    def observe(self, name: str, value: float) -> None:
        self.samples[name].append(round(value, 1))

    def summary(self) -> dict[str, Any]:
        def percentiles(values: deque[float]) -> dict[str, Any]:
            ordered = sorted(values)
            return {
                "count": len(ordered),
                "p50_ms": ordered[(len(ordered) - 1) // 2],
                "p95_ms": ordered[min(len(ordered) - 1, int(len(ordered) * 0.95))],
            }

        return {
            "counters": dict(self.counters),
            "latency": {k: percentiles(v) for k, v in self.samples.items() if v},
            "first_audible_audio_ms": None,
            "unacknowledged_messages_heard": None,
            "sound_test_confirmation_rate": (
                self.counters["sound_tests_confirmed"] / self.counters["sound_tests"]
                if self.counters["sound_tests"]
                else None
            ),
        }


class Evidence:
    """Opaque fetch tokens bind a hosted media URL to one call and endpoint."""

    def __init__(self, metrics: Metrics) -> None:
        self.metrics = metrics
        self.tokens: OrderedDict[str, tuple[CallRecord, str, str, float]] = OrderedDict()
        self.watchers: list[Any] = []

    def watch(self, hass: HomeAssistant, record: CallRecord, entity_id: str, media_id: str) -> None:
        """Only attribute a playing event when its content ID matches this exact call."""
        started = time.monotonic()
        entry = er.async_get(hass).async_get(entity_id)
        route = entry.platform if entry else "media_player"

        def changed(event) -> None:
            state = event.data.get("new_state")
            if (
                state is not None
                and state.state == "playing"
                and state.attributes.get("media_content_id") == media_id
            ):
                elapsed = round((time.monotonic() - started) * 1000)
                record.evidence.setdefault(entity_id, {})["playback_reported_ms"] = elapsed
                self.metrics.observe(f"playback_reported:{route}", elapsed)
                cleanup()

        unsubscribe = async_track_state_change_event(hass, [entity_id], changed)

        def cleanup(_now=None) -> None:
            unsubscribe()
            cancel_timer()
            if cleanup in self.watchers:
                self.watchers.remove(cleanup)

        cancel_timer = async_call_later(hass, 180, cleanup)
        self.watchers.append(cleanup)
        while len(self.watchers) > 2048:
            self.watchers[0]()

    def close(self) -> None:
        for cleanup in list(self.watchers):
            cleanup()

    def url(self, hass: HomeAssistant, url: str, record: CallRecord, entity_id: str) -> str:
        parts = urlsplit(url)
        if not parts.path.startswith(("/api/roomcall/clip/", "/api/roomcall/live/")):
            return url
        token = secrets.token_urlsafe(24)
        directory, filename = parts.path.rsplit("/", 1)
        stem, separator, extension = filename.rpartition(".")
        if not separator:
            stem, extension = filename, "mp3"
        stem = f"{directory}/{stem}"
        path = f"{stem}.{token}.{extension}"
        self.tokens[token] = (record, entity_id, path, time.monotonic())
        while len(self.tokens) > 2048:
            self.tokens.popitem(last=False)
        # Speakers cannot sign in: they get a short-lived signed path, the way Home Assistant
        # serves media sources, and the clip/live views keep requiring authentication.
        # HEOS receivers can strip ampersands from URLs. Bind endpoint evidence in the
        # filename so authSig is the only query parameter, with no separator to corrupt.
        signed = async_sign_path(
            hass,
            path,
            timedelta(seconds=CLIP_TTL_SECONDS),
            use_content_user=True,
        )
        return f"{parts.scheme}://{parts.netloc}{signed}"

    def fetched(self, token: str | None, path: str) -> None:
        found = self.tokens.get(token or "")
        if found is None:
            return
        record, entity_id, expected, started = found
        if expected != path or time.monotonic() - started > 180:
            return
        evidence = record.evidence.setdefault(entity_id, {})
        if "audio_fetched_ms" not in evidence:
            elapsed = (time.monotonic() - started) * 1000
            evidence["audio_fetched_ms"] = round(elapsed)
            self.metrics.observe("audio_fetch", elapsed)
            self.metrics.counters["audio_fetches"] += 1
