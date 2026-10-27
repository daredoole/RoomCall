"""Paired TECHO5 endpoints and consent-based browser/physical intercom calls."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import ipaddress
import secrets
from dataclasses import dataclass
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.storage import Store

from .core import friendly, speaker_area
from .echo_devices import EchoDevices
from .echo_transport import FRAME_BYTES, EchoLink
from .errors import Problem


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

    async def pair(self, speaker: str, host: str, word: str) -> None:
        word = word.strip()
        address = ipaddress.ip_address(host)
        networks = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
        if not any(address in ipaddress.ip_network(net) for net in networks):
            raise Problem("invalid_request")
        if (
            speaker not in self.data.speakers
            or not 16 <= len(word) <= 63
            or "\n" in word
            or "\r" in word
        ):
            raise Problem("invalid_request")
        registry = er.async_get(self.hass)
        entity = registry.async_get(speaker)
        if not entity or entity.platform != "esphome" or not entity.device_id:
            raise Problem("invalid_request")
        answers = [
            e.entity_id
            for e in er.async_entries_for_device(registry, entity.device_id)
            if e.domain == "button" and e.original_name == "Answer call"
        ]
        if len(answers) != 1:
            raise Problem("invalid_request")
        self.peers[speaker] = {"host": str(address), "word": word, "answer": answers[0]}
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
            self.cancel_invite(invitation, owner)
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

    async def _run(self, call: EchoCall, name: str) -> None:
        reason = "ended"
        try:
            call.emit({"type": "calling", "id": call.id})
            peer = self.peers[call.speaker]
            async with asyncio.timeout(900):
                call.link = await EchoLink.connect(peer["host"], 8181, peer["word"], name)
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
        except asyncio.CancelledError:
            raise
        except Exception:  # Do not expose remote errors, addresses, or keys to browser events.
            reason = "station_unavailable"
        finally:
            if call.link:
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(call.link.close(), 1)
            if self.calls.pop(call.id, None):
                call.emit({"type": "ended", "id": call.id, "reason": reason})

    def end(self, call_id: str, reason: str = "ended") -> None:
        if call := self.calls.pop(call_id, None):
            if call.task:
                call.task.cancel()
            call.emit({"type": "ended", "id": call_id, "reason": reason})

    def invite(self, speaker: str, station: Any) -> str:
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
            "timer": async_call_later(
                self.hass, 60, lambda _: self.cancel_invite(invite_id, station.owner)
            ),
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
        self.invitations.pop(invitation)
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
