"""First-run URL repairs and explicit, persisted per-room listening results."""

from __future__ import annotations

import ssl
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp
import pytest
from custom_components.roomcall import repairs
from custom_components.roomcall.communication import execute
from custom_components.roomcall.diagnostics import async_get_config_entry_diagnostics
from custom_components.roomcall.onboarding import Setup
from custom_components.roomcall.protocol import SETUP_KEY
from homeassistant.core import Context
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_mock_service


@pytest.mark.parametrize(
    ("url", "reason"),
    [
        ("http://localhost:8123", "url_loopback"),
        ("http://host.localhost:8123", "url_loopback"),
        ("http://127.0.0.1:8123", "url_loopback"),
        ("http://127.42.5.6:8123", "url_loopback"),
        ("http://[::1]:8123", "url_loopback"),
        ("http://0.0.0.0:8123", "url_loopback"),
        ("http://HOME-ASSISTANT.local.:8123", "url_mdns"),
        ("http://172.17.0.1:8123", "url_container"),
        ("http://172.17.255.254:8123", "url_container"),
        ("http://192.168.1.2:8123", None),
        ("http://ha.example.com:8123", None),
        ("http://[fd00::2]:8123", None),
        # RFC1918 LAN addresses are not necessarily Docker bridges.
        ("http://172.16.1.2:8123", None),
    ],
)
async def test_url_patterns_raise_and_clear_fixable_repairs(hass, url, reason):
    await async_setup_component(hass, "roomcall", {})
    with patch.object(repairs, "get_url", return_value=url):
        await repairs.async_check_urls(hass)
    issue = ir.async_get(hass).async_get_issue("roomcall", repairs.ISSUE_UNREACHABLE_URL)
    if reason:
        assert issue and issue.is_fixable
        assert issue.translation_placeholders["url"] == url
        assert issue.translation_placeholders["reason"] != reason
    else:
        assert issue is None
    with patch.object(repairs, "get_url", return_value="http://192.168.1.2:8123"):
        await repairs.async_check_urls(hass)
    assert not any(
        ir.async_get(hass).async_get_issue("roomcall", key) for key in repairs.URL_ISSUES
    )


@pytest.mark.parametrize("failure", [None, "certificate", "connection", "timeout"])
async def test_https_certificate_trust_is_checked_without_claiming_speaker_access(hass, failure):
    await async_setup_component(hass, "roomcall", {})
    session = MagicMock()
    response = session.head.return_value
    if failure == "certificate":
        response.__aenter__.side_effect = aiohttp.ClientConnectorCertificateError(
            MagicMock(), ssl.SSLCertVerificationError("self-signed certificate")
        )
    elif failure == "connection":
        response.__aenter__.side_effect = aiohttp.ClientConnectionError("unreachable")
    elif failure == "timeout":
        response.__aenter__.side_effect = TimeoutError
    with (
        patch.object(repairs, "get_url", return_value="https://ha.example.com"),
        patch.object(repairs, "async_get_clientsession", return_value=session),
    ):
        await repairs.async_check_urls(hass)
    issue = ir.async_get(hass).async_get_issue("roomcall", repairs.ISSUE_HTTPS_INTERNAL_URL)
    if failure:
        assert issue and issue.is_fixable
        assert (
            "certificate" if failure == "certificate" else "unknown"
        ) in issue.translation_placeholders["reason"]
    else:
        assert issue is None
    assert session.head.call_args.kwargs["allow_redirects"] is False


@pytest.mark.parametrize("issue_id", repairs.URL_ISSUES)
@pytest.mark.parametrize("confirm", [True, False])
async def test_repair_flow_requires_confirmation_and_cancel_preserves_url(hass, issue_id, confirm):
    await async_setup_component(hass, "roomcall", {})
    await async_setup_component(hass, "repairs", {})
    hass.config.internal_url = "http://localhost:8123"
    ir.async_create_issue(
        hass,
        "roomcall",
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=issue_id,
    )
    manager = hass.data["repairs"]["flow_manager"]
    with patch.object(repairs, "async_get_source_ip", AsyncMock(return_value="192.168.1.20")):
        result = await manager.async_init(
            "roomcall", context={"issue_id": issue_id}, data={"issue_id": issue_id}
        )
    assert result["type"] == "form"
    proposal = result["description_placeholders"]["suggested_url"]
    assert proposal == "http://192.168.1.20:8123"
    assert hass.config.internal_url == "http://localhost:8123"
    result = await manager.async_configure(result["flow_id"], {"confirm": confirm})
    if confirm:
        assert result["type"] == "create_entry"
        assert hass.config.internal_url == proposal
        assert ir.async_get(hass).async_get_issue("roomcall", issue_id) is None
    else:
        assert result["type"] == "abort" and result["reason"] == "cancelled"
        assert hass.config.internal_url == "http://localhost:8123"
        assert ir.async_get(hass).async_get_issue("roomcall", issue_id)


async def test_repair_never_overwrites_a_concurrent_network_change(hass):
    flow = await repairs.async_create_fix_flow(hass, repairs.ISSUE_NO_URL, None)
    flow.hass = hass
    with patch.object(repairs, "async_get_source_ip", AsyncMock(return_value="192.168.1.20")):
        assert (await flow.async_step_init())["type"] == "form"
    hass.config.internal_url = "http://192.168.1.25:8123"
    result = await flow.async_step_confirm({"confirm": True})
    assert result["reason"] == "url_changed"
    assert hass.config.internal_url == "http://192.168.1.25:8123"
    with pytest.raises(ValueError):
        await repairs.async_create_fix_flow(hass, "missing_speakers", None)


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "172.17.0.2", "169.254.0.2", "224.0.0.1", "invalid"]
)
async def test_bad_source_ip_cannot_be_suggested(hass, address):
    with patch.object(repairs, "async_get_source_ip", AsyncMock(return_value=address)):
        assert await repairs.suggested_url(hass) is None


async def test_missing_network_and_direct_https_need_manual_configuration(hass):
    with patch.object(repairs, "async_get_source_ip", AsyncMock(side_effect=HomeAssistantError)):
        flow = repairs.URLRepairFlow()
        flow.hass = hass
        assert (await flow.async_step_init())["reason"] == "no_lan_url"
    hass.config.api = SimpleNamespace(use_ssl=True)
    assert await repairs.suggested_url(hass) is None
    hass.config.api = SimpleNamespace(use_ssl=False, port=9000)
    with patch.object(repairs, "async_get_source_ip", AsyncMock(return_value="fd00::2")):
        assert await repairs.suggested_url(hass) == "http://[fd00::2]:9000"
    with patch.object(repairs, "get_url", side_effect=NoURLAvailableError):
        await repairs.async_check_urls(hass)
    assert ir.async_get(hass).async_get_issue("roomcall", repairs.ISSUE_NO_URL).is_fixable


async def call(hass, user, **request):
    return await execute(hass, "setup", request, Context(user_id=user.id))


async def test_room_reports_persist_and_agree_in_tools_and_diagnostics(
    hass, setup_roomcall, hass_admin_user
):
    user = hass_admin_user
    played = async_mock_service(hass, "media_player", "play_media")
    draft = await call(hass, user, action="plan", media_only=True)
    plan = draft["plan"]
    await call(hass, user, action="apply", id=plan["id"], revision=plan["revision"])
    await hass.async_block_till_done()
    entities = plan["settings"]["speakers"]
    for entity, result in zip(entities, ["heard", "not_heard", "skipped"], strict=True):
        if result == "skipped":
            assert (await call(hass, user, action="skip", id=plan["id"], speaker=entity))["ok"]
            continue
        tested = await call(hass, user, action="test", id=plan["id"], speaker=entity)
        assert tested["ok"], tested
        await hass.data["roomcall"].jobs.items[tested["id"]].task
        # A successful playback request never confirms audibility.
        checked = await call(hass, user, action="verify", id=plan["id"])
        assert not next(
            e for e in checked["verification"]["endpoints"] if e["entity_id"] == entity
        )["heard"]
        confirmed = await call(
            hass, user, action="confirm", id=plan["id"], speaker=entity, heard=result == "heard"
        )
        assert confirmed["ok"], confirmed
    assert len(played) == 2
    assert all("/chime/ding.mp3" in c.data["media_content_id"] for c in played)
    checked = (await call(hass, user, action="verify", id=plan["id"]))["verification"]
    assert [e["result"] for e in checked["endpoints"]] == ["heard", "not_heard", "skipped"]
    assert not checked["audio_confirmed"] and not checked["completed"]
    assert "troubleshooting" in checked["endpoints"][1]["troubleshooting_url"]
    diagnostics = await async_get_config_entry_diagnostics(hass, setup_roomcall)
    discovered = (await call(hass, user, action="discover"))["discovery"]["audio_verification"]
    assert diagnostics["audio_verification"] == discovered
    assert diagnostics["audio_verification"]["endpoints"] == checked["endpoints"]
    restored = Setup(hass)
    await restored.load()
    assert restored.audio_verification(setup_roomcall) == discovered
    # Retesting removes the previous Yes before a new result exists.
    retested = await call(hass, user, action="test", id=plan["id"], speaker=entities[0])
    assert retested["ok"]
    await hass.data["roomcall"].jobs.items[retested["id"]].task
    checked = (await call(hass, user, action="verify", id=plan["id"]))["verification"]
    assert checked["endpoints"][0]["result"] == "untested"
    await hass.async_block_till_done()
    assert (await call(hass, user, action="skip", id=plan["id"], speaker=entities[0]))["ok"]
    stale = await call(hass, user, action="confirm", id=plan["id"], speaker=entities[0], heard=True)
    assert stale["error"]["code"] == "not_found"
    hass.config_entries.async_update_entry(setup_roomcall, options={"volume": 0.1})
    assert not restored.audio_verification(setup_roomcall)["configuration_current"]
    assert not restored.audio_verification(setup_roomcall)["audio_confirmed"]
    hass.data.pop(SETUP_KEY)
    assert (await async_get_config_entry_diagnostics(hass, setup_roomcall))[
        "audio_verification"
    ] is None
