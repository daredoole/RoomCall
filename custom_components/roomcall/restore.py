"""Put a speaker back the way it was after a call.

Speakers without announce support (AV receivers through HEOS, Android TV / Google TV boxes
through Cast) stop whatever they were doing to play a call. RoomCall snapshots the speaker and
the other media players that control the same physical device (for example the Denon's
``denonavr`` entity, which owns the input source, or an onn box's Android TV Remote entity,
which knows the foreground app), then restores input, volume, power and app afterwards.
"""

from __future__ import annotations

import asyncio
import logging
from asyncio import sleep as settling_delay
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from homeassistant.components.media_player import MediaPlayerEntityFeature
from homeassistant.const import STATE_OFF, STATE_PLAYING, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import Context, HomeAssistant, callback
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.event import async_track_state_change_event

from .errors import Problem
from .playback import controlled_entities, route, source_matches

_LOGGER = logging.getLogger(__name__)

POLL_SECONDS = 0.5
UNKNOWN_LENGTH_SECONDS = 45.0
# After a live call ends, receivers still play what they buffered.
LIVE_TAIL_SECONDS = 5.0
# Unknown length and never reported "playing": assume a short message took about this long.
UNSEEN_PLAYING_SECONDS = 12.0
CONTROL_TIMEOUT = 10.0
# HA's "on"/NET state can precede the amplifier's audible output by several seconds.
AVR_STARTUP_SECONDS = 10.0
AVR_RESTORE_SECONDS = 2.0
_ACTIVE = "roomcall_restore_active"
CAST_RECEIVER_APPS = {"com.google.android.apps.mediashell", "com.google.cast.receiver"}


@dataclass(slots=True)
class Snapshot:
    entity_id: str
    platform: str | None
    state: str
    source: str | None
    volume: float | None
    app_id: str | None
    muted: bool | None = None
    media_id: str | None = None
    media_type: str | None = None
    position: float | None = None


def related_players(hass: HomeAssistant, entity_id: str) -> list[str]:
    """Verified physical companions plus an explicitly configured audio controller."""
    return controlled_entities(hass, entity_id)


def take(hass: HomeAssistant, entity_ids: list[str]) -> list[Snapshot]:
    registry = er.async_get(hass)
    snaps = []
    for entity_id in entity_ids:
        state = hass.states.get(entity_id)
        if state is None:
            continue
        entry = registry.async_get(entity_id)
        snaps.append(
            Snapshot(
                entity_id=entity_id,
                platform=entry.platform if entry else None,
                state=state.state,
                source=state.attributes.get("source"),
                volume=state.attributes.get("volume_level"),
                app_id=state.attributes.get("app_id"),
                muted=state.attributes.get("is_volume_muted"),
                media_id=state.attributes.get("media_content_id"),
                media_type=state.attributes.get("media_content_type"),
                position=state.attributes.get("media_position"),
            )
        )
    return snaps


async def wait_until_done(hass: HomeAssistant, entity_id: str, seconds: float | None) -> None:
    """Wait for the call to finish: its length when known, else until playback stops.

    Some players (HEOS receivers) never report "playing" for an announcement. When the length
    is unknown and the player has not been seen playing, wait a safe minimum instead of
    trusting "not playing" right away.
    """
    known = seconds is not None
    minimum = (seconds or 0) + 0.8 if known else 2.0
    deadline = minimum + (8.0 if known else UNKNOWN_LENGTH_SECONDS)
    waited = 0.0
    seen_playing = False
    while waited < deadline:
        await asyncio.sleep(POLL_SECONDS)
        waited += POLL_SECONDS
        state = hass.states.get(entity_id)
        playing = state is not None and state.state == STATE_PLAYING
        seen_playing = seen_playing or playing
        if waited < minimum or playing:
            continue
        if known or seen_playing or waited >= UNSEEN_PLAYING_SECONDS:
            return


async def restore(
    hass: HomeAssistant,
    snaps: list[Snapshot],
    context: Context | None = None,
    assistant: str | None = None,
) -> list[str]:
    failed = []
    for snap in snaps:
        current = hass.states.get(snap.entity_id)
        if current is None or current.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            failed.append(snap.entity_id)
            continue
        try:
            await _restore_one(hass, snap, context, assistant)
        except Exception as err:
            failed.append(snap.entity_id)
            _LOGGER.warning("RoomCall could not restore %s: %s", snap.entity_id, err)
    return failed


async def _call(
    hass: HomeAssistant,
    service: str,
    entity_id: str,
    *,
    context: Context | None = None,
    assistant: str | None = None,
    **data: Any,
) -> None:
    from .communication import permitted

    if context is not None and not await permitted(hass, entity_id, context, assistant):
        raise Problem("forbidden")
    async with asyncio.timeout(CONTROL_TIMEOUT):
        await hass.services.async_call(
            "media_player",
            service,
            {"entity_id": entity_id, **data},
            blocking=True,
            context=context,
        )


async def _restore_one(
    hass: HomeAssistant,
    snap: Snapshot,
    context: Context | None = None,
    assistant: str | None = None,
) -> None:
    current = hass.states.get(snap.entity_id)
    if current is None or current.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
        return
    features = current.attributes.get("supported_features", 0)
    if current.state == STATE_OFF:
        return
    sources = current.attributes.get("source_list") or []
    if (
        snap.muted is not None
        and current.attributes.get("is_volume_muted") != snap.muted
        and features & MediaPlayerEntityFeature.VOLUME_MUTE
    ):
        await _call(
            hass,
            "volume_mute",
            snap.entity_id,
            is_volume_muted=snap.muted,
            context=context,
            assistant=assistant,
        )
    if (
        snap.source
        and snap.source != current.attributes.get("source")
        and snap.source in sources
        and features & MediaPlayerEntityFeature.SELECT_SOURCE
    ):
        await _call(
            hass,
            "select_source",
            snap.entity_id,
            source=snap.source,
            context=context,
            assistant=assistant,
        )
    if (
        snap.volume is not None
        and current.attributes.get("volume_level") != snap.volume
        and features & MediaPlayerEntityFeature.VOLUME_SET
    ):
        await _call(
            hass,
            "volume_set",
            snap.entity_id,
            volume_level=snap.volume,
            context=context,
            assistant=assistant,
        )
    if (
        snap.app_id
        and snap.app_id not in CAST_RECEIVER_APPS
        and snap.state != STATE_OFF
        and snap.platform == "androidtv_remote"
        and current.attributes.get("app_id") != snap.app_id
    ):
        # Reopen the app that was in front (playback position is up to the app).
        await _call(
            hass,
            "play_media",
            snap.entity_id,
            media_content_type="app",
            media_content_id=snap.app_id,
            context=context,
            assistant=assistant,
        )
    if snap.state == STATE_OFF and features & MediaPlayerEntityFeature.TURN_OFF:
        await _call(hass, "turn_off", snap.entity_id, context=context, assistant=assistant)


class Guard:
    """Snapshot before a call and restore after it, sharing one snapshot when calls overlap."""

    def __init__(
        self,
        hass: HomeAssistant,
        entity_id: str,
        *,
        owner: str | None = None,
        assistant: str | None = None,
        restore_enabled: bool = True,
        evidence: dict[str, Any] | None = None,
        call_id: str | None = None,
    ) -> None:
        self._hass = hass
        self._entity_id = entity_id
        self._active: dict[str, list[Any]] = hass.data.setdefault(_ACTIVE, {})
        self.owner, self.assistant = owner, assistant
        self.context = Context(user_id=owner)
        self.restore_enabled = restore_enabled
        self.evidence = evidence if evidence is not None else {}
        self.media_id = ""
        self.call_id = call_id
        self.entities: list[str] = []
        self.output: dict[str, str] = {}
        self.delays: dict[str, float] = {}

    def start(self) -> None:
        self.entities = controlled_entities(self._hass, self._entity_id)
        self.output = route(self._hass, self._entity_id)
        self.delays = dict(
            getattr(self._hass.data.get("roomcall"), "playback_delays", {}).get(self._entity_id, {})
        )
        if entry := self._active.get(self._entity_id):
            entry[1] += 1
        else:
            snaps = take(self._hass, self.entities)
            self._active[self._entity_id] = [snaps, 1]

    async def _permitted(self) -> bool:
        from .communication import permitted

        return all(
            [
                await permitted(self._hass, entity, Context(user_id=self.owner), self.assistant)
                for entity in self.entities
            ]
        )

    async def _control(self, entity_id: str, service: str, **values: Any) -> None:
        if not await self._permitted():
            raise Problem("forbidden")
        await _call(
            self._hass, service, entity_id, context=self.context, assistant=self.assistant, **values
        )

    async def _wait(self, entity_id: str, matches: Callable[[Any], bool]) -> None:
        ready = asyncio.Event()

        @callback
        def changed(event) -> None:
            state = event.data.get("new_state")
            if state is not None and matches(state):
                ready.set()

        unsubscribe = async_track_state_change_event(self._hass, [entity_id], changed)
        try:
            current = self._hass.states.get(entity_id)
            if current is not None and matches(current):
                return
            async with asyncio.timeout(CONTROL_TIMEOUT):
                await ready.wait()
        finally:
            unsubscribe()

    async def prepare(self, media_id: str, volume: float | None) -> None:
        """Wake verified companions and select the explicitly known audible input."""
        self.media_id = media_id
        output = self.output
        if output.get("error"):
            raise Problem(output["error"])
        if not await self._permitted():
            raise Problem("forbidden")
        controllers = [
            s.entity_id
            for s in self._active[self._entity_id][0]
            if s.platform == "androidtv_remote"
        ]
        if output.get("controller"):
            controllers.append(output["controller"])
        changed = False
        try:
            for entity in dict.fromkeys(controllers):
                if not any(s.entity_id == entity for s in self._active[self._entity_id][0]):
                    raise Problem("playback_prepare_failed")
                state = self._hass.states.get(entity)
                if state is None or state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
                    raise Problem("playback_prepare_failed")
                if state.state == STATE_OFF:
                    if (
                        not state.attributes.get("supported_features", 0)
                        & MediaPlayerEntityFeature.TURN_ON
                    ):
                        raise Problem("playback_prepare_failed")
                    await self._control(entity, "turn_on")
                    changed = True
                    await self._wait(
                        entity,
                        lambda s: s.state not in (STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN),
                    )
                    # HA can hide standby attributes. Capture them after wake, before overrides.
                    awake = self._hass.states.get(entity)
                    snapshot = next(
                        s for s in self._active[self._entity_id][0] if s.entity_id == entity
                    )
                    for field, attribute in (
                        ("source", "source"),
                        ("volume", "volume_level"),
                        ("muted", "is_volume_muted"),
                    ):
                        if getattr(snapshot, field) is None:
                            setattr(snapshot, field, awake.attributes.get(attribute))
            controller = output.get("controller", self._entity_id)
            state = self._hass.states.get(controller)
            if source := output.get("source"):
                sources = state.attributes.get("source_list") or [] if state else []
                selected_source = (
                    source
                    if source in sources
                    else next(
                        (
                            item
                            for item in sources
                            if source_matches(self._hass, controller, source, item)
                        ),
                        None,
                    )
                )
                if state is None or selected_source is None:
                    raise Problem("playback_prepare_failed")
                if not source_matches(
                    self._hass, controller, source, state.attributes.get("source")
                ):
                    await self._control(controller, "select_source", source=selected_source)
                    changed = True
                    await self._wait(
                        controller,
                        lambda s: source_matches(
                            self._hass, controller, source, s.attributes.get("source")
                        ),
                    )
            if state is not None:
                features = state.attributes.get("supported_features", 0)
                if volume is not None and features & MediaPlayerEntityFeature.VOLUME_SET:
                    await self._control(controller, "volume_set", volume_level=volume)
                if (
                    state.attributes.get("is_volume_muted")
                    and features & MediaPlayerEntityFeature.VOLUME_MUTE
                ):
                    await self._control(controller, "volume_mute", is_volume_muted=False)
        except Problem:
            raise
        except Exception as err:
            raise Problem("playback_prepare_failed") from err
        entry = er.async_get(self._hass).async_get(output.get("controller", self._entity_id))
        avr = bool(entry and entry.platform == "denonavr")
        startup = self.delays.get("startup_delay", AVR_STARTUP_SECONDS if avr and changed else 0)
        self.delays.setdefault("restore_delay", AVR_RESTORE_SECONDS if avr else 0)
        self.evidence["interruption"] = {"prepared": True, "restore": "pending"}
        if startup:
            await settling_delay(startup)
        if not await self._permitted():
            raise Problem("forbidden")
        # Do not dispatch after somebody turns the receiver off or changes inputs during warm-up.
        state = self._hass.states.get(output.get("controller", self._entity_id))
        if output.get("source") and (
            state is None
            or state.state in (STATE_OFF, STATE_UNAVAILABLE, STATE_UNKNOWN)
            or not source_matches(
                self._hass, state.entity_id, output["source"], state.attributes.get("source")
            )
        ):
            raise Problem("playback_prepare_failed")
        self.evidence["playback_delays"] = {
            "startup_delay": startup,
            "restore_delay": self.delays["restore_delay"],
        }

    async def _resume(self, snaps: list[Snapshot]) -> None:
        original = next((s for s in snaps if s.entity_id == self._entity_id), None)
        if original is None or original.state not in ("playing", "paused"):
            return
        if original.media_id:
            previous = urlsplit(original.media_id)
            if previous.path.startswith(("/api/roomcall/", "/api/tts_proxy/")) or (
                previous.scheme == "media-source" and previous.netloc == "tts"
            ):
                return  # Never replay a previous announcement as background media.
        android = next(
            (
                s
                for s in snaps
                if s.platform == "androidtv_remote"
                and s.app_id
                and s.app_id not in CAST_RECEIVER_APPS
            ),
            None,
        )
        if android is not None:
            target = android.entity_id
            await self._wait(target, lambda state: state.attributes.get("app_id") == android.app_id)
        elif (
            original.platform == "cast"
            and original.media_id
            and original.media_id.startswith(("http://", "https://", "media-source://"))
            and original.media_type
        ):
            target = original.entity_id
            extra: dict[str, Any] = {"autoplay": original.state == "playing"}
            features = self._hass.states.get(target).attributes.get("supported_features", 0)
            if original.position is not None and features & MediaPlayerEntityFeature.SEEK:
                extra["current_time"] = original.position
            await _call(
                self._hass,
                "play_media",
                target,
                media_content_id=original.media_id,
                media_content_type=original.media_type,
                context=self.context,
                assistant=self.assistant,
                extra=extra,
            )
        else:
            return
        current = self._hass.states.get(target)
        feature = (
            MediaPlayerEntityFeature.PLAY
            if original.state == "playing"
            else MediaPlayerEntityFeature.PAUSE
        )
        if current.attributes.get("supported_features", 0) & feature:
            await _call(
                self._hass,
                "media_play" if original.state == "playing" else "media_pause",
                target,
                context=self.context,
                assistant=self.assistant,
            )

    async def abort(self) -> None:
        """Roll back failed preparation without waiting for audio that was never dispatched."""
        await self._release(True)

    async def _release(self, rollback: bool = False) -> None:
        entry = self._active[self._entity_id]
        entry[1] -= 1
        if entry[1]:
            return
        try:
            current = self._hass.states.get(self._entity_id)
            original = next((s for s in entry[0] if s.entity_id == self._entity_id), None)
            replaced = (
                original is not None
                and original.platform == "cast"
                and current
                and current.attributes.get("media_content_id")
                not in (None, self.media_id, original.media_id)
            )
            output = self.output
            controller = self._hass.states.get(output.get("controller", self._entity_id))
            if self.evidence.get("interruption", {}).get("prepared") and output.get("source"):
                replaced = (
                    replaced
                    or controller is None
                    or controller.state == STATE_OFF
                    or (
                        controller.attributes.get("source") is not None
                        and not source_matches(
                            self._hass,
                            controller.entity_id,
                            output["source"],
                            controller.attributes.get("source"),
                        )
                    )
                )
            for snap in entry[0]:
                if snap.platform == "androidtv_remote" and snap.app_id:
                    current_app = self._hass.states.get(snap.entity_id)
                    if current_app and current_app.attributes.get("app_id") not in {
                        None,
                        snap.app_id,
                        *CAST_RECEIVER_APPS,
                    }:
                        replaced = True
            if not await self._permitted() or (self.media_id and replaced):
                self.evidence["interruption"] = {"restore": "skipped_external_change_or_permission"}
                return
            if self.restore_enabled or rollback:
                failed = await restore(self._hass, entry[0], self.context, self.assistant)
                try:
                    if not rollback:
                        await self._resume(entry[0])
                except Exception:
                    failed.append(self._entity_id)
                self.evidence["interruption"] = {
                    "restore": "failed" if failed else "requests_completed",
                    "failed_entities": failed,
                    "resume_semantics": "best_effort_not_exact_session",
                }
            else:
                self.evidence["interruption"] = {"restore": "disabled"}
        finally:
            del self._active[self._entity_id]
            if self.call_id:
                self._hass.bus.async_fire("roomcall_delivery_updated", {"id": self.call_id})
                self._hass.bus.async_fire("roomcall_job_updated", {"id": self.call_id})

    async def finish(
        self,
        seconds: float | None,
        until: Callable[[], Awaitable[None]] | None = None,
        *,
        audio_seconds: Callable[[], float] | None = None,
    ) -> None:
        """Restore once the call is over.

        ``until`` waits for a live call to end; its length is unknown up front, and some
        receivers (HEOS) never report "playing", so guessing from their state cut calls off.
        """
        try:
            if until is not None:
                await until()
                await asyncio.sleep(LIVE_TAIL_SECONDS)
                await wait_until_done(
                    self._hass, self._entity_id, audio_seconds() if audio_seconds else 0
                )
            else:
                await wait_until_done(self._hass, self._entity_id, seconds)
            if self.delays.get("restore_delay"):
                await settling_delay(self.delays["restore_delay"])
        finally:
            await self._release()
