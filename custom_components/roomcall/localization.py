"""Shared translated copy for services, Assist and the dashboard card."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

from homeassistant.core import HomeAssistant


def catalog(language: str) -> dict[str, str]:
    language = language.split("-")[0]
    if language not in {"en", "es", "de", "fr", "nl"}:
        language = "en"
    return _catalog(language)


@lru_cache(maxsize=5)
def _catalog(language: str) -> dict[str, str]:
    path = Path(__file__).parent / "translations" / f"{language}.json"
    return json.loads(path.read_text())["selector"]["communication"]["options"]


async def async_load(hass: HomeAssistant) -> None:
    for language in ("en", "es", "de", "fr", "nl"):
        await hass.async_add_executor_job(catalog, language)


def text(hass: HomeAssistant, key: str, language: str | None = None, **values: object) -> str:
    return catalog(language or hass.config.language).get(key, key).format(**values)
