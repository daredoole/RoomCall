"""Repairs: problems RoomCall can see but cannot fix on its own."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.start import async_at_started

from .const import DOMAIN
from .core import friendly

ISSUE_MISSING_SPEAKERS = "missing_speakers"
ISSUE_HTTPS_INTERNAL_URL = "https_internal_url"
ISSUE_NO_URL = "no_internal_url"
# Speakers reboot and integrations reconnect; only report one that stays unavailable.
UNAVAILABLE_GRACE_SECONDS = 300


def _missing(hass: HomeAssistant, speakers: list[str]) -> list[str]:
    return [
        e for e in speakers if (s := hass.states.get(e)) is None or s.state == STATE_UNAVAILABLE
    ]


@callback
def async_watch(hass: HomeAssistant, speakers: list[str]) -> Callable[[], None]:
    """Check URLs once Home Assistant has started, and watch the speakers from then on."""
    pending: Callable[[], None] | None = None

    @callback
    def check_later() -> None:
        nonlocal pending
        if pending is None:
            pending = async_call_later(hass, UNAVAILABLE_GRACE_SECONDS, run)

    @callback
    def run(_now: datetime | None = None) -> None:
        nonlocal pending
        pending = None
        async_check(hass, speakers)

    @callback
    def changed(_event: Event[EventStateChangedData]) -> None:
        if _missing(hass, speakers):
            check_later()
        else:
            ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING_SPEAKERS)

    @callback
    def started(_hass: HomeAssistant) -> None:
        async_check_urls(hass)
        if _missing(hass, speakers):
            check_later()
        else:
            ir.async_delete_issue(hass, DOMAIN, ISSUE_MISSING_SPEAKERS)

    unsub_start = async_at_started(hass, started)
    unsub_state = async_track_state_change_event(hass, speakers, changed)

    @callback
    def cancel() -> None:
        unsub_start()
        unsub_state()
        if pending is not None:
            pending()

    return cancel


def async_check(hass: HomeAssistant, speakers: list[str]) -> None:
    """Raise or clear the unavailable-speakers issue."""
    missing = _missing(hass, speakers)
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


def async_check_urls(hass: HomeAssistant) -> None:
    """Raise or clear issues about the URL speakers download calls from."""
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
