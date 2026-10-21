"""Opt-in, bounded voicemail in Home Assistant storage; never an external audio URL."""

from __future__ import annotations

import base64
import secrets
import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.storage import Store

from .errors import Problem


class Mailbox:
    def __init__(self, hass: HomeAssistant, entry_id: str, enabled: bool, retention: int) -> None:
        self.hass = hass
        self.store = Store[dict[str, Any]](hass, 1, f"roomcall.{entry_id}.voicemail")
        self.enabled = enabled
        self.retention = retention
        self.items: dict[str, dict[str, Any]] = {}
        self.cancel_timer = None

    async def load(self) -> None:
        if not self.enabled:
            await self.store.async_remove()
            return
        loaded = await self.store.async_load()
        self.items = (loaded or {}).get("items", {})
        await self.save()

    async def save(self) -> None:
        now = time.time()
        self.items = {key: item for key, item in self.items.items() if item["expires"] > now}
        if self.enabled:
            await self.store.async_save({"items": self.items})
        self.close()
        if self.items and self.enabled:
            delay = max(0.1, min(item["expires"] for item in self.items.values()) - now)
            self.cancel_timer = async_call_later(
                self.hass,
                delay,
                lambda _: self.hass.async_create_background_task(
                    self.save(), "roomcall voicemail expiry"
                ),
            )

    def close(self) -> None:
        if self.cancel_timer:
            self.cancel_timer()
            self.cancel_timer = None

    async def add(
        self,
        owner: str | None,
        message: str,
        target: str,
        audio: bytes | None = None,
        request: dict[str, Any] | None = None,
    ) -> str:
        if not self.enabled:
            raise Problem("voicemail_disabled")
        key = secrets.token_urlsafe(16)
        self.items[key] = {
            "id": key,
            "owner": owner,
            "message": message,
            "target": target,
            "request": request or {"areas": target.split(", ")},
            "created": time.time(),
            "expires": time.time() + self.retention,
            "audio": base64.b64encode(audio).decode()
            if audio and len(audio) <= 1024 * 1024
            else None,
        }
        while len(self.items) > 20:
            self.items.pop(next(iter(self.items)))
        await self.save()
        return key

    def list(self, owner: str | None) -> list[dict[str, Any]]:
        return [
            {k: v for k, v in item.items() if k not in {"owner", "audio"}}
            for item in self.items.values()
            if item["owner"] == owner and item["expires"] > time.time()
        ]

    async def delete(self, key: str) -> None:
        self.items.pop(key, None)
        await self.save()
