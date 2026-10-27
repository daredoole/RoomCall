"""Paired TECHO5 endpoints and consent-based browser/physical intercom calls."""

from __future__ import annotations

import array
import asyncio
import base64
import contextlib
import ipaddress
import logging
import math
import secrets
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .core import friendly, speaker_area
from .echo_devices import EchoDevices
from .echo_transport import FRAME_BYTES, EchoLink, WordRefused
from .errors import Problem

_LOGGER = logging.getLogger(__name__)


@dataclass
class EchoCall:
    id: str
    owner: str
    speaker: str
    emit: Any
    invitation: str | None = None
    task: asyncio.Task | None = None
    link: EchoLink | None = None
    accepted: bool = False
    # Audio evidence for diagnosis: frames and summed squares, per direction. Never audibility.
    heard: list[float] = field(default_factory=lambda: [0, 0.0])
    said: list[float] = field(default_factory=lambda: [0, 0.0])

    def summary(self) -> str:
        return f"from Echo {_level(self.heard)}, to Echo {_level(self.said)}"


class Echoes:
    """Keys remain in HA storage, never discovery, diagnostics, events or URLs."""

    def __init__(self, hass: HomeAssistant, data: Any) -> None:
        self.hass, self.data = hass, data
        self.store = Store(hass, 1, f"roomcall.{data.entry_id}.echo")
        self.peers: dict[str, dict[str, Any]] = {}
        self.calls: dict[str, EchoCall] = {}
        self.invitations: dict[str, dict[str, Any]] = {}
        self.devices = EchoDevices(hass, data)

    async def load(self) -> None:
        self.peers = await self.store.async_load() or {}
        await self.devices.load()

    def list(self) -> list[dict[str, Any]]:
        return [
            {
                "id": "echo:" + speaker,
                "speaker": speaker,
                "name": friendly(self.hass, speaker),
                "room": speaker_area(self.hass, speaker),
                "capability": "techo5_pcm",
                "consent": "required",
                "available": bool(
                    (state := self.hass.states.get(speaker))
                    and state.state not in ("unavailable", "unknown")
                ),
            }
            for speaker in self.peers
            if speaker in self.data.speakers
        ]

    def candidates(self) -> list[dict[str, Any]]:
        """Unpaired TECHO5 speakers, so setup never offers an ordinary ESPHome speaker."""
        return [
            {
                "speaker": speaker,
                "name": friendly(self.hass, speaker),
                "host_known": self._esphome_host(speaker) is not None,
            }
            for speaker in self.data.speakers
            if speaker not in self.peers and self._answer(speaker)
        ]

    def _answer(self, speaker: str) -> str | None:
        registry = er.async_get(self.hass)
        entity = registry.async_get(speaker)
        if not entity or entity.platform != "esphome" or not entity.device_id:
            return None
        answers = [
            e.entity_id
            for e in er.async_entries_for_device(registry, entity.device_id)
            if e.domain == "button" and e.original_name == "Answer call"
        ]
        return answers[0] if len(answers) == 1 else None

    def _esphome_host(self, speaker: str) -> str | None:
        # ESPHome keeps its entry's host current after DHCP changes; prefer it to a typed address.
        entity = er.async_get(self.hass).async_get(speaker)
        entry = entity and self.hass.config_entries.async_get_entry(entity.config_entry_id or "")
        return _private(entry.data.get("host", "")) if entry else None

    async def pair(self, speaker: str, host: str, word: str) -> None:
        word = word.strip()
        address = _private(host.strip()) if host.strip() else self._esphome_host(speaker)
        if (
            address is None
            or speaker not in self.data.speakers
            or not 16 <= len(word) <= 63
            or "\n" in word
            or "\r" in word
        ):
            raise Problem("invalid_request")
        if (answer := self._answer(speaker)) is None:
            raise Problem("invalid_request")
        self.peers[speaker] = {"host": address, "word": word, "answer": answer}
        await self.store.async_save(self.peers)

    async def remove(self, speaker: str) -> None:
        self.peers.pop(speaker, None)
        for call in list(self.calls.values()):
            if call.speaker == speaker:
                self.end(call.id, "disconnected")
        await self.store.async_save(self.peers)

    def start(
        self, speaker: str, owner: str, name: str, emit: Any, invitation: str | None = None
    ) -> str:
        if speaker not in self.peers or speaker not in self.data.speakers:
            raise Problem("station_unavailable")
        if len(self.calls) >= 8 or any(c.speaker == speaker for c in self.calls.values()):
            raise Problem("station_busy")
        if invitation:
            invite = self.invitations.get(invitation)
            if not invite or invite["speaker"] != speaker or invite["owner"] != owner:
                raise Problem("forbidden")
            # The first answer wins: stop ringing every other station in the same room.
            for other, item in list(self.invitations.items()):
                if item["group"] == invite["group"]:
                    self._drop(other)
        call = EchoCall(secrets.token_urlsafe(24), owner, speaker, emit, invitation)
        self.calls[call.id] = call
        call.task = self.hass.async_create_background_task(self._run(call, name), "RoomCall Echo")
        return call.id

    def owned(self, call_id: str, owner: str) -> EchoCall:
        if (call := self.calls.get(call_id)) is None:
            raise Problem("not_found")
        if call.owner != owner:
            raise Problem("forbidden")
        return call

    async def audio(self, call_id: str, owner: str, encoded: str) -> None:
        call = self.owned(call_id, owner)
        if not call.accepted or call.link is None:
            raise Problem("consent_required")
        try:
            pcm = base64.b64decode(encoded, validate=True)
        except ValueError as err:
            raise Problem("invalid_request") from err
        if len(pcm) not in (FRAME_BYTES, FRAME_BYTES * 2):
            raise Problem("invalid_request")
        for offset in range(0, len(pcm), FRAME_BYTES):
            await call.link.send(b"S", pcm[offset : offset + FRAME_BYTES])
        _count(call.said, pcm)

    async def _run(self, call: EchoCall, name: str) -> None:
        reason = "ended"
        try:
            call.emit({"type": "calling", "id": call.id})
            peer = self.peers[call.speaker]
            host = self._esphome_host(call.speaker) or peer["host"]
            async with asyncio.timeout(900):
                call.link = await EchoLink.connect(host, 8181, peer["word"], name)
                while True:
                    kind, payload = await asyncio.wait_for(
                        call.link.receive(), 5 if call.accepted else 40
                    )
                    if kind == b"R":
                        call.emit({"type": "ringing", "id": call.id})
                        if call.invitation:
                            await self.hass.services.async_call(
                                "button", "press", {"entity_id": peer["answer"]}, blocking=True
                            )
                    elif kind == b"A":
                        call.accepted = True
                        call.emit({"type": "accepted", "id": call.id})
                    elif kind == b"S" and call.accepted and len(payload) in (640, 1280):
                        _count(call.heard, payload)
                        call.emit(
                            {
                                "type": "audio",
                                "id": call.id,
                                "pcm": base64.b64encode(payload).decode("ascii"),
                            }
                        )
                    elif kind == b"X":
                        break
                    elif kind in (b"B", b"D", b"N"):
                        reason = "station_busy" if kind == b"B" else "declined"
                        break
                    else:
                        raise ValueError("unexpected intercom message")
        except TimeoutError:
            reason = "timeout"
        except WordRefused:
            reason = "echo_word_mismatch"
        except asyncio.CancelledError:
            raise
        except Exception:  # Do not expose remote errors, addresses, or keys to browser events.
            reason = "station_unavailable"
        finally:
            if call.link:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(call.link.close(), 1)
            _LOGGER.info("Echo call %s ended (%s): %s", call.speaker, reason, call.summary())
            if self.calls.pop(call.id, None):
                call.emit({"type": "ended", "id": call.id, "reason": reason})

    def end(self, call_id: str, reason: str = "ended") -> None:
        if call := self.calls.pop(call_id, None):
            if call.task:
                call.task.cancel()
            call.emit({"type": "ended", "id": call_id, "reason": reason})

    def invite(self, speaker: str, station: Any, group: str | None = None) -> str:
        """A physical voice request invites a browser; Answer is still required there."""
        from homeassistant.helpers.event import async_call_later

        if speaker not in self.peers:
            raise Problem("station_unavailable")
        if len(self.invitations) >= 16:
            raise Problem("queue_full")
        invite_id = secrets.token_urlsafe(24)
        self.invitations[invite_id] = {
            "owner": station.owner,
            "speaker": speaker,
            "station": station,
            "group": group or invite_id,
            "timer": async_call_later(self.hass, 60, lambda _: self._drop(invite_id)),
        }
        station.emit(
            {
                "type": "echo_incoming",
                "id": invite_id,
                "speaker": speaker,
                "from": friendly(self.hass, speaker),
            }
        )
        return invite_id

    def cancel_invite(self, invitation: str, owner: str) -> None:
        invite = self.invitations.get(invitation)
        if not invite:
            raise Problem("not_found")
        if invite["owner"] != owner:
            raise Problem("forbidden")
        self._drop(invitation)

    def _drop(self, invitation: str) -> None:
        invite = self.invitations.pop(invitation)
        invite["timer"]()
        invite["station"].emit({"type": "echo_cancelled", "id": invitation})

    async def close(self) -> None:
        tasks = [call.task for call in self.calls.values() if call.task]
        for call_id in list(self.calls):
            self.end(call_id, "disconnected")
        for invite in self.invitations.values():
            invite["timer"]()
        self.invitations.clear()
        self.devices.listeners.clear()
        await asyncio.gather(*tasks, return_exceptions=True)


def _private(host: str) -> str | None:
    """Only a literal private-network address; never a name or a public host."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return None
    networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
    return str(address) if any(address in ipaddress.ip_network(n) for n in networks) else None


def _count(total: list[float], pcm: bytes) -> None:
    samples = array.array("h", pcm)
    total[0] += 1
    total[1] += sum(value * value for value in samples) / len(samples)


def _level(total: list[float]) -> str:
    if not total[0]:
        return "no audio"
    mean = total[1] / total[0]
    level = 10 * math.log10(mean / 32768**2) if mean else -math.inf
    return f"{int(total[0])} frames at {level:.0f} dBFS"
