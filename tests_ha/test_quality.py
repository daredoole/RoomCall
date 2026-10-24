"""Diagnostics, repairs and per-room notify entities."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, patch

from custom_components.roomcall.diagnostics import async_get_config_entry_diagnostics
from homeassistant.core import HomeAssistant
from homeassistant.core_config import async_process_ha_core_config
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
    async_mock_service,
)

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


async def test_diagnostics_redact_people_and_agent_accounts(hass: HomeAssistant, house):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"speakers": ["media_player.kitchen_hub"]},
        options={
            "communication": {
                "agent_policies": {"user-id-123": {"operations": ["send"]}},
                "preferences": {"person.alex": {"language": "es"}},
                "household_profiles": {
                    "dinner": {"name": "Dinner", "defaults": {"people": ["person.alex"]}}
                },
            }
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, entry)
    communication = diag["options"]["communication"]
    for key in ("agent_policies", "preferences", "household_profiles"):
        assert communication[key] == "**REDACTED**"
    assert "user-id-123" not in str(diag) and "person.alex" not in str(diag)


async def test_unavailable_speaker_raises_a_repair_after_a_grace_period(hass: HomeAssistant, house):
    hass.states.async_set("media_player.basement_sonos", "unavailable")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"speakers": ["media_player.basement_sonos", "media_player.kitchen_hub"]},
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    issues = ir.async_get(hass)
    # Not straight away: speakers reboot and integrations reconnect after a restart.
    assert issues.async_get_issue(DOMAIN, "missing_speakers") is None

    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()
    issue = issues.async_get_issue(DOMAIN, "missing_speakers")
    assert issue is not None
    assert "basement sonos" in issue.translation_placeholders["speakers"].lower()

    # Cleared as soon as the speaker is back.
    hass.states.async_set("media_player.basement_sonos", "idle", {"supported_features": 1048576})
    await hass.async_block_till_done()
    assert issues.async_get_issue(DOMAIN, "missing_speakers") is None


async def test_speaker_that_comes_back_quickly_is_not_reported(hass: HomeAssistant, house):
    hass.states.async_set("media_player.basement_sonos", "unavailable")
    entry = MockConfigEntry(domain=DOMAIN, data={"speakers": ["media_player.basement_sonos"]})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    hass.states.async_set("media_player.basement_sonos", "idle", {"supported_features": 1048576})
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=301))
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, "missing_speakers") is None


async def test_https_internal_url_raises_a_repair(hass: HomeAssistant, house):
    await async_process_ha_core_config(hass, {"internal_url": "https://ha.example.test"})
    entry = MockConfigEntry(domain=DOMAIN, data={"speakers": ["media_player.kitchen_hub"]})
    entry.add_to_hass(hass)
    with patch(
        "custom_components.roomcall.repairs.certificate_problem",
        AsyncMock(return_value="url_certificate"),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    issue = ir.async_get(hass).async_get_issue(DOMAIN, "https_internal_url")
    assert issue is not None
    assert issue.translation_placeholders["url"] == "https://ha.example.test"


async def test_room_notify_entity_speaks_in_that_room(
    hass: HomeAssistant, setup_roomcall, house, tts_audio
):
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
    assert "Laundry%20is%20done" in tts_audio.await_args.args[1]
    assert "/api/roomcall/clip/" in played[0].data["media_content_id"]
