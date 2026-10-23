"""Household profiles expand into ordinary permission-checked messages."""

from unittest.mock import patch

import pytest
from custom_components.roomcall.communication import execute
from custom_components.roomcall.config_flow import validate_communication
from custom_components.roomcall.errors import Problem
from custom_components.roomcall.messages import request_for
from custom_components.roomcall.profiles import validate
from custom_components.roomcall.schema import vol
from homeassistant.core import Context
from pytest_homeassistant_custom_component.common import async_mock_service


async def test_profiles_preview_send_overrides_and_isolated_voice(hass, setup_roomcall, tts_audio):
    data = hass.data["roomcall"]
    hass.states.async_set("tts.piper", "unknown")
    data.chime_url = "http://ha.local/custom-chime.mp3"
    data.household_profiles = validate(
        {
            "dinner": {
                "name": "Dinner",
                "defaults": {"areas": ["kitchen"], "message": "Dinner is ready", "volume": 0.2},
                "speech": {"engine": "tts.piper", "voice": "en_US-lessac-medium", "chime": "off"},
            }
        }
    )
    validate_communication({"household_profiles": data.household_profiles})
    found = await execute(hass, "discover", {}, Context())
    assert found["profiles"][0]["id"] == "dinner"
    preview = await execute(hass, "preview", {"profile": "dinner"}, Context())
    assert len(preview["endpoints"]) == 2 and not tts_audio.await_count
    override = await execute(
        hass, "preview", {"profile": "dinner", "areas": ["basement"]}, Context()
    )
    assert override["endpoints"] == ["media_player.basement_sonos"]
    played = async_mock_service(hass, "media_player", "play_media")
    sent = await execute(hass, "send", {"profile": "dinner"}, Context())
    job = data.jobs.items[sent["id"]]
    await job.task
    assert job.record.message == "Dinner is ready" and len(played) == 2
    assert "voice=en_US-lessac-medium" in played[0].data["media_content_id"]
    assert data.chime_style == "chirp" and data.tts_engine is None
    with pytest.raises(Problem, match="profile_missing"):
        await request_for(hass, data, {"profile": "missing"}, Context())
    assert (await execute(hass, "send", {"profile": "missing"}, Context()))["error"][
        "code"
    ] == "profile_missing"
    with patch("custom_components.roomcall.communication.permitted", return_value=False):
        assert not (await execute(hass, "discover", {}, Context()))["profiles"]
    hass.data.pop("roomcall")
    assert (await execute(hass, "send", {"profile": "dinner"}, Context()))["error"][
        "code"
    ] == "not_ready"
    hass.data["roomcall"] = data


@pytest.mark.parametrize(
    "profiles",
    [
        {"empty": {"name": "Empty", "defaults": {}}},
        {str(i): {"name": "x", "defaults": {}} for i in range(33)},
        {"dinner": {"name": "Dinner", "defaults": {"profile": "dinner"}}},
        {"dinner": {"name": "Dinner", "defaults": {"volume": 2}}},
        {"empty": {"name": "Empty", "defaults": {}}},
        {"invalid id": {"name": "Dinner", "defaults": {}}},
    ],
)
def test_invalid_profiles_are_rejected(profiles):
    with pytest.raises(vol.Invalid):
        validate(profiles)


async def test_options_normalize_single_destinations_before_discovery(hass, setup_roomcall):
    from custom_components.roomcall import settings_from_options

    configured = settings_from_options(
        {
            "communication": {
                "household_profiles": {
                    "dinner": {
                        "name": "Dinner",
                        "defaults": {"areas": "kitchen", "message": "Dinner"},
                    }
                }
            }
        }
    )
    assert configured.household_profiles["dinner"]["defaults"]["areas"] == ["kitchen"]
