"""Configuration file (YAML) with sensible defaults for zero-config use."""

from __future__ import annotations

import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import Speaker, SpeakerKind


@dataclass(slots=True)
class Config:
    host: str = "0.0.0.0"
    port: int = 8765
    public_url: str | None = None
    token: str | None = None
    volume: float | None = None
    gain_db: float = 0.0
    discovery_timeout: float = 4.0
    rooms: dict[str, list[str]] = field(default_factory=dict)
    static_speakers: list[Speaker] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None) -> Config:
        if path is None or not path.exists():
            return cls()
        raw: dict[str, Any] = yaml.safe_load(path.read_text()) or {}
        static = [
            Speaker(
                id=f"{s['kind']}:{s['host']}",
                name=s.get("name", s["host"]),
                kind=SpeakerKind(s["kind"]),
                host=s["host"],
                port=int(s.get("port", 0)),
            )
            for s in raw.get("speakers", [])
        ]
        volume = raw.get("volume")
        return cls(
            host=raw.get("host", "0.0.0.0"),
            port=int(raw.get("port", 8765)),
            public_url=raw.get("public_url"),
            token=raw.get("token"),
            volume=float(volume) if volume is not None else None,
            gain_db=float(raw.get("gain_db", 0.0)),
            discovery_timeout=float(raw.get("discovery_timeout", 4.0)),
            rooms={k: list(v) for k, v in (raw.get("rooms") or {}).items()},
            static_speakers=static,
        )

    def base_url(self) -> str:
        """URL speakers use to fetch clips; must be reachable from the speakers' network."""
        if self.public_url:
            return self.public_url.rstrip("/")
        return f"http://{lan_ip()}:{self.port}"


def lan_ip() -> str:
    """Best-effort primary LAN IPv4 address (no packets are sent)."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        try:
            sock.connect(("192.0.2.1", 9))
            return str(sock.getsockname()[0])
        except OSError:
            return "127.0.0.1"
