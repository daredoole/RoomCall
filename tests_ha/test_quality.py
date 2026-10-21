"""Diagnostics, repairs and per-room notify entities."""

from __future__ import annotations

from custom_components.roomcall.diagnostics import async_get_config_entry_diagnostics
from homeassistant.core import HomeAssistant
from homeassistant.core_config import async_process_ha_core_config
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_mock_service

DOMAIN = "roomcall"


async def test_diagnostics_hide_what_people_said(hass: HomeAssistant, setup_roomcall, house):
    async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("tts.piper", "unknown")
    await hass.services.async_call(
        DOMAIN,
        "broadcast",
        {"areas": [house["basement"]], "message": "secret plans"},
        blocking=True,
    )
    diag = await async_get_config_entry_diagnostics(hass, setup_roomcall)
    assert diag["version"]
    assert {r["name"] for r in diag["rooms"]} == {"Basement", "Kitchen"}
    assert len(diag["speakers"]) == 3
    assert diag["speakers"][0]["announce"] is True
    assert "secret plans" not in str(diag)
    assert diag["recent_calls"][0]["text"] == "**REDACTED**"


async def test_unavailable_speaker_raises_a_repair(hass: HomeAssistant, house):
    hass.states.async_set("media_player.basement_sonos", "unavailable")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"speakers": ["media_player.basement_sonos", "media_player.kitchen_hub"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "missing_speakers")
    assert issue is not None
    assert "basement sonos" in issue.translation_placeholders["speakers"].lower()

    hass.states.async_set("media_player.basement_sonos", "idle", {"supported_features": 1048576})
    assert await hass.config_entries.async_reload(entry.entry_id)
    assert ir.async_get(hass).async_get_issue(DOMAIN, "missing_speakers") is None


async def test_https_internal_url_raises_a_repair(hass: HomeAssistant, house):
    await async_process_ha_core_config(hass, {"internal_url": "https://ha.home.arpa"})
    entry = MockConfigEntry(domain=DOMAIN, data={"speakers": ["media_player.kitchen_hub"]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "https_internal_url")
    assert issue is not None
    assert issue.translation_placeholders["url"] == "https://ha.home.arpa"


async def test_room_notify_entity_speaks_in_that_room(hass: HomeAssistant, setup_roomcall, house):
    played = async_mock_service(hass, "media_player", "play_media")
    hass.states.async_set("tts.piper", "unknown")
    assert hass.states.get("notify.roomcall_basement") is not None
    assert hass.states.get("notify.roomcall_kitchen") is not None
    await hass.services.async_call(
        "notify",
        "send_message",
        {"entity_id": "notify.roomcall_basement", "message": "Laundry is done"},
        blocking=True,
    )
    assert [c.data["entity_id"] for c in played] == ["media_player.basement_sonos"]
    assert "Laundry%20is%20done" in played[0].data["media_content_id"]
