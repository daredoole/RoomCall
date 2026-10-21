"""Repairs: problems RoomCall can see but cannot fix on its own."""

from __future__ import annotations

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError, get_url

from .const import DOMAIN
from .core import friendly

ISSUE_MISSING_SPEAKERS = "missing_speakers"
ISSUE_HTTPS_INTERNAL_URL = "https_internal_url"
ISSUE_NO_URL = "no_internal_url"


def async_check(hass: HomeAssistant, speakers: list[str]) -> None:
    """Raise or clear repair issues for the current setup."""
    missing = [
        e for e in speakers if (s := hass.states.get(e)) is None or s.state == STATE_UNAVAILABLE
    ]
    if missing:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_MISSING_SPEAKERS,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_MISSING_SPEAKERS,
            translation_placeholders={"speakers": ", ".join(friendly(hass, e) for e in missing)},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING_SPEAKERS)

    try:
        url = get_url(hass, allow_internal=True, allow_external=True, prefer_external=False)
    except NoURLAvailableError:
        url = None
    if url is None:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_NO_URL,
            is_fixable=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_NO_URL,
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_NO_URL)
    if url and url.startswith("https://"):
        # Speakers fetch clips from this URL and usually reject home-made certificates.
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_HTTPS_INTERNAL_URL,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_HTTPS_INTERNAL_URL,
            translation_placeholders={"url": url},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_HTTPS_INTERNAL_URL)
