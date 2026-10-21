"""Stable machine-readable errors shared by all communication interfaces."""

from __future__ import annotations

from typing import Any

from homeassistant.core import HomeAssistant

from .localization import text


class Problem(Exception):
    """A bounded public error; raw provider exceptions are not sent to clients."""

    def __init__(self, code: str, *, retryable: bool = False, **details: Any) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable
        self.details = details

    def as_dict(self, hass: HomeAssistant) -> dict[str, Any]:
        from .protocol import next_actions

        return {
            "ok": False,
            "error": {
                "code": self.code,
                "message": text(hass, self.code),
                "retryable": self.retryable,
                "details": self.details,
                "next_actions": next_actions(self.code),
            },
        }
