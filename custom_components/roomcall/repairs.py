"""Repairs: problems RoomCall can see but cannot fix on its own."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from ipaddress import ip_address, ip_network
from urllib.parse import urlsplit

import aiohttp
from homeassistant.components.network import async_get_source_ip
from homeassistant.components.repairs.models import RepairsFlow
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.event import async_call_later, async_track_state_change_event
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.start import async_at_started

from .const import DOMAIN
from .core import friendly
from .localization import text
from .schema import vol

ISSUE_MISSING_SPEAKERS = "missing_speakers"
ISSUE_HTTPS_INTERNAL_URL = "https_internal_url"
ISSUE_NO_URL = "no_internal_url"
ISSUE_UNREACHABLE_URL = "unreachable_internal_url"
URL_ISSUES = (ISSUE_NO_URL, ISSUE_HTTPS_INTERNAL_URL, ISSUE_UNREACHABLE_URL)
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

    async def started(_hass: HomeAssistant) -> None:
        await async_check_urls(hass)
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


def url_problem(url: str) -> str | None:
    """Recognize address patterns; this is a warning, not a speaker reachability test."""
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    if host == "localhost" or host.endswith(".localhost"):
        return "url_loopback"
    if host.endswith(".local"):
        return "url_mdns"
    try:
        address = ip_address(host)
    except ValueError:
        return None
    if address.is_loopback or address.is_unspecified:
        return "url_loopback"
    # Docker's default bridge. Custom bridges can use other ranges; a successful
    # human listening test is still needed even for an address that passes here.
    if address.version == 4 and address in ip_network("172.17.0.0/16"):
        return "url_container"
    return None


async def certificate_problem(hass: HomeAssistant, url: str) -> str | None:
    """Check using public CA trust; HTTP status says nothing about audibility."""
    try:
        async with async_get_clientsession(hass).head(
            url, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=5)
        ):
            return None
    except aiohttp.ClientSSLError:
        return "url_certificate"
    except (aiohttp.ClientError, TimeoutError):
        return "url_tls_unverified"


async def async_check_urls(hass: HomeAssistant) -> None:
    """Raise or clear issues about the actual URL used by http.py and calls.py."""
    try:
        url = get_url(hass, allow_internal=True, allow_external=True, prefer_external=False)
    except NoURLAvailableError:
        url = None
    reason = url_problem(url) if url else "speaker_url_missing"
    if url and not reason and url.startswith("https://"):
        reason = await certificate_problem(hass, url)
    issue_id = (
        ISSUE_NO_URL
        if not url
        else ISSUE_HTTPS_INTERNAL_URL
        if url.startswith("https://")
        else ISSUE_UNREACHABLE_URL
    )
    for key in URL_ISSUES:
        if key == issue_id and reason:
            ir.async_create_issue(
                hass,
                DOMAIN,
                key,
                is_fixable=True,
                severity=ir.IssueSeverity.WARNING if url else ir.IssueSeverity.ERROR,
                translation_key=key,
                translation_placeholders={
                    "url": url or text(hass, "url_missing"),
                    "reason": text(hass, reason),
                },
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, key)


async def suggested_url(hass: HomeAssistant) -> str | None:
    """Suggest the network integration's source IP, never an unusable HTTP downgrade."""
    if hass.config.api and hass.config.api.use_ssl:
        return None
    try:
        address = ip_address(await async_get_source_ip(hass))
    except (HomeAssistantError, ValueError):
        return None
    host = f"[{address}]" if address.version == 6 else str(address)
    port = hass.config.api.port if hass.config.api else 8123
    url = f"http://{host}:{port}"
    return None if url_problem(url) or address.is_link_local or address.is_multicast else url


class URLRepairFlow(RepairsFlow):
    """Changing HA's shared internal URL always needs a concrete confirmation."""

    def __init__(self, issue_id: str = ISSUE_NO_URL) -> None:
        super().__init__()
        self.url_issue = issue_id

    async def async_step_init(self, user_input=None):
        self.before = self.hass.config.internal_url
        self.proposal = await suggested_url(self.hass)
        return await self.async_step_confirm()

    async def async_step_confirm(self, user_input=None):
        if not self.proposal:
            return self.async_abort(reason="no_lan_url")
        if user_input is not None:
            if not user_input["confirm"]:
                return self.async_abort(reason="cancelled")
            if self.hass.config.internal_url != self.before:
                return self.async_abort(reason="url_changed")
            await self.hass.config.async_update(internal_url=self.proposal)
            await async_check_urls(self.hass)
            return self.async_create_entry(data={})
        issue = ir.async_get(self.hass).async_get_issue(DOMAIN, self.url_issue)
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({vol.Required("confirm", default=False): bool}),
            description_placeholders={
                **(
                    issue.translation_placeholders
                    if issue and issue.translation_placeholders
                    else {
                        "url": self.before or text(self.hass, "url_missing"),
                        "reason": text(self.hass, "speaker_url_missing"),
                    }
                ),
                "current_url": self.before or text(self.hass, "url_missing"),
                "suggested_url": self.proposal,
            },
        )


async def async_create_fix_flow(hass: HomeAssistant, issue_id: str, data) -> RepairsFlow:
    """Provide a fix flow for each URL issue."""
    if issue_id not in URL_ISSUES:
        raise ValueError(issue_id)
    return URLRepairFlow(issue_id)
