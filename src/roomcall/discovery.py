"""Find Sonos, AirPlay and Chromecast speakers on the LAN with mDNS (zeroconf)."""

from __future__ import annotations

import asyncio
import ipaddress
import logging
from collections.abc import Iterable, Mapping
from typing import Any

from .models import KIND_PRIORITY, Speaker, SpeakerKind

_LOGGER = logging.getLogger(__name__)

SERVICE_TYPES: dict[str, SpeakerKind] = {
    "_sonos._tcp.local.": SpeakerKind.SONOS,
    "_raop._tcp.local.": SpeakerKind.AIRPLAY,
    "_airplay._tcp.local.": SpeakerKind.AIRPLAY,
    "_googlecast._tcp.local.": SpeakerKind.CAST,
}


def _decode(properties: Mapping[Any, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in properties.items():
        k = key.decode(errors="replace") if isinstance(key, bytes) else key
        if value is None:
            continue
        out[k] = value.decode(errors="replace") if isinstance(value, bytes) else value
    return out


def _instance_name(service_type: str, name: str) -> str:
    """Strip the service type suffix from a zeroconf instance name."""
    instance = name.removesuffix("." + service_type).removesuffix(service_type)
    # RAOP and Sonos instances are "<device id>@<friendly name>".
    if service_type.startswith(("_raop.", "_sonos.")) and "@" in instance:
        instance = instance.split("@", 1)[1]
    return instance.strip(". ")


def _first_ipv4(addresses: Iterable[str]) -> str | None:
    for addr in addresses:
        try:
            if ipaddress.ip_address(addr).version == 4:
                return addr
        except ValueError:
            continue
    return None


def speaker_from_service(
    service_type: str,
    name: str,
    addresses: Iterable[str],
    port: int,
    properties: Mapping[Any, Any],
) -> Speaker | None:
    """Build a :class:`Speaker` from one zeroconf service record, or ``None`` if unusable."""
    kind = SERVICE_TYPES.get(service_type)
    host = _first_ipv4(addresses)
    if kind is None or host is None:
        return None
    props = _decode(properties)
    friendly = _instance_name(service_type, name)
    model: str | None
    if kind is SpeakerKind.CAST:
        friendly = props.get("fn", friendly)
        model = props.get("md")
        speaker_id = props.get("id", f"{host}:{port}")
    elif kind is SpeakerKind.SONOS:
        model = props.get("mdl") or props.get("model")
        speaker_id = props.get("hhid") or props.get("uuid") or f"{host}:{port}"
    else:
        model = props.get("am") or props.get("model")
        speaker_id = props.get("deviceid") or props.get("pk") or f"{host}:{port}"
    return Speaker(
        id=f"{kind.value}:{speaker_id}",
        name=friendly,
        kind=kind,
        host=host,
        port=port,
        model=model,
    )


def merge_speakers(found: Iterable[Speaker]) -> list[Speaker]:
    """Keep one entry per physical device (by host), preferring the best protocol."""
    best: dict[str, Speaker] = {}
    for speaker in found:
        current = best.get(speaker.host)
        if current is None or KIND_PRIORITY[speaker.kind] < KIND_PRIORITY[current.kind]:
            best[speaker.host] = speaker
    return sorted(best.values(), key=lambda s: (s.name.lower(), s.kind.value))


async def discover(duration: float = 4.0) -> list[Speaker]:
    """Browse the LAN for ``duration`` seconds and return the deduplicated speakers."""
    from zeroconf import ServiceStateChange
    from zeroconf.asyncio import AsyncServiceBrowser, AsyncServiceInfo, AsyncZeroconf

    found: list[Speaker] = []
    pending: set[asyncio.Task[None]] = set()
    azc = AsyncZeroconf()

    async def resolve(service_type: str, name: str) -> None:
        info = AsyncServiceInfo(service_type, name)
        if not await info.async_request(azc.zeroconf, 3000):
            return
        speaker = speaker_from_service(
            service_type,
            name,
            info.parsed_addresses(),
            info.port or 0,
            info.properties or {},
        )
        if speaker is not None:
            found.append(speaker)

    def on_change(
        zeroconf: object, service_type: str, name: str, state_change: ServiceStateChange
    ) -> None:
        if state_change is ServiceStateChange.Added:
            task = asyncio.ensure_future(resolve(service_type, name))
            pending.add(task)
            task.add_done_callback(pending.discard)

    browser = AsyncServiceBrowser(azc.zeroconf, list(SERVICE_TYPES), handlers=[on_change])
    try:
        await asyncio.sleep(duration)
        if pending:
            await asyncio.wait(pending, timeout=3)
    finally:
        await browser.async_cancel()
        await azc.async_close()
    _LOGGER.debug("Discovered %d service records", len(found))
    return merge_speakers(found)
