"""Fixtures for the Home Assistant integration tests (pytest-homeassistant-custom-component)."""

from __future__ import annotations

import io
import math
import struct
import wave
from pathlib import Path
from unittest.mock import AsyncMock

import custom_components
import pytest
from homeassistant.core import HomeAssistant
from homeassistant.core_config import async_process_ha_core_config
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

DOMAIN = "roomcall"

# The harness ships its own (empty) custom_components package; expose ours through it.
_OURS = str(Path(__file__).resolve().parents[1] / "custom_components")
if _OURS not in custom_components.__path__:
    custom_components.__path__.append(_OURS)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
async def assistant_exposure_registry(hass):
    """Conversation is a real dependency; initialize its normal Core prerequisite."""
    from homeassistant.components.homeassistant.exposed_entities import ExposedEntities

    registry = ExposedEntities(hass)
    await registry.async_initialize()
    hass.data["homeassistant.exposed_entities"] = registry


@pytest.fixture
async def house(hass: HomeAssistant) -> dict[str, str]:
    """Two areas, three speakers: two in the kitchen, one in the basement."""
    await async_process_ha_core_config(hass, {"internal_url": "http://ha.local:8123"})
    areas = ar.async_get(hass)
    kitchen = areas.async_create("Kitchen")
    basement = areas.async_create("Basement")
    registry = er.async_get(hass)
    speakers = {
        "media_player.kitchen_sonos": kitchen.id,
        "media_player.kitchen_hub": kitchen.id,
        "media_player.basement_sonos": basement.id,
    }
    for entity_id, area_id in speakers.items():
        entry = registry.async_get_or_create(
            "media_player",
            "test",
            entity_id.split(".")[1],
            suggested_object_id=entity_id.split(".")[1],
        )
        registry.async_update_entity(entry.entity_id, area_id=area_id)
        hass.states.async_set(
            entity_id,
            "idle",
            {"friendly_name": entity_id.split(".")[1].title(), "supported_features": 1048576 | 512},
        )
    return {"kitchen": kitchen.id, "basement": basement.id}


@pytest.fixture
async def setup_roomcall(hass: HomeAssistant, house: dict[str, str]) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            "speakers": [
                "media_player.kitchen_sonos",
                "media_player.kitchen_hub",
                "media_player.basement_sonos",
            ],
            "volume": 0.4,
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


@pytest.fixture(autouse=True)
def receiver_settling(monkeypatch):
    """Hardware settling is a clock delay; timing tests explicitly gate dispatch/restore."""
    delay = AsyncMock()
    monkeypatch.setattr("custom_components.roomcall.restore.settling_delay", delay)
    return delay


@pytest.fixture(autouse=True)
def mock_native_playback_duration(monkeypatch):
    """Mocked media players do not produce playback events; queue tests set their own gate.

    Receiver restoration and real ffmpeg/audio tests keep their actual implementations.
    """
    monkeypatch.setattr("custom_components.roomcall.scheduler.wait_for_playback", AsyncMock())


@pytest.fixture(autouse=True)
def tts_audio(monkeypatch):
    """Mock the external voice provider; all chime composition uses real ffmpeg."""
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(22050)
        wav.writeframes(
            b"".join(
                struct.pack("<h", int(6000 * math.sin(i * 2 * math.pi * 440 / 22050)))
                for i in range(22050)
            )
        )
    provider = AsyncMock(return_value=("wav", output.getvalue()))
    monkeypatch.setattr(
        "custom_components.roomcall.messages.async_get_media_source_audio", provider
    )
    return provider
