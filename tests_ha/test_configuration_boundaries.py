"""Setup, version compatibility and announcement recovery contracts."""

from __future__ import annotations

import builtins
import importlib.util
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
import voluptuous
from custom_components import roomcall
from custom_components.roomcall import announcements, config_flow, repairs, restore
from custom_components.roomcall.announcements import Announcement
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.network import NoURLAvailableError
from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_options_invalid_speakers_and_settings(hass, setup_roomcall):
    result = await hass.config_entries.options.async_init(setup_roomcall.entry_id)
    result = await hass.config_entries.options.async_configure(result["flow_id"], {"speakers": []})
    assert result["errors"] == {"speakers": "no_speakers"}
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {"speakers": ["media_player.kitchen_sonos"], "communication": {"aliases": "not-json"}},
    )
    assert result["errors"] == {"base": "invalid_settings"}
    with pytest.raises(NotImplementedError):
        config_flow._AnnouncementFlow()._schema({})


async def test_suggestions_skip_mirrored_players(hass, house):
    registry = er.async_get(hass)
    entity = registry.async_get_or_create(
        "media_player", "music_assistant", "mirror", original_name="Mirror"
    )
    hass.states.async_set(entity.entity_id, "idle", {"supported_features": 1048576})
    assert entity.entity_id not in config_flow.suggested_speakers(hass)


async def test_card_registration_success_and_fallback(hass, house):
    hass.config.components.add("frontend")
    entry = MockConfigEntry(domain="roomcall", data={"speakers": ["media_player.kitchen_sonos"]})
    entry.add_to_hass(hass)
    callbacks = []
    with patch.object(
        roomcall, "async_at_started", side_effect=lambda hass, callback: callbacks.append(callback)
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
    assert len(callbacks) == 1
    with patch.object(roomcall.frontend, "async_register_card", AsyncMock()) as register:
        await callbacks[0](hass)
        register.assert_awaited_once_with(hass)
    with (
        patch.object(
            roomcall.frontend,
            "async_register_card",
            AsyncMock(side_effect=RuntimeError("unavailable")),
        ),
        patch.object(roomcall, "add_extra_js_url") as fallback,
    ):
        await callbacks[0](hass)
        fallback.assert_called_once_with(hass, roomcall.frontend.card_url())


async def test_missing_url_repair(hass):
    with patch.object(repairs, "get_url", side_effect=NoURLAvailableError):
        repairs.async_check_urls(hass)
    assert ir.async_get(hass).async_get_issue("roomcall", repairs.ISSUE_NO_URL)


async def test_announcement_validation_pending_cleanup_and_provider_error(hass, setup_roomcall):
    announcer = hass.data["roomcall"].announcer
    invalid = Announcement("bad", "Bad", "scheduled", {"time": "invalid"})
    announcer._schedule(invalid)
    announcer._watch(Announcement("missing", "Missing", "triggered", {}))
    assert announcements._seconds(2.5) == 2.5
    item = Announcement(
        "watch",
        "Watched",
        "triggered",
        {
            "entity": "switch.test",
            "to_state": "on",
            "for": 2,
            "message": "Hello",
            "target_speakers": ["media_player.kitchen_sonos"],
            "people": ["person.alex"],
        },
    )
    announcer.items[item.subentry_id] = item
    announcer._watch(item)
    hass.states.async_set("switch.test", "unknown")
    await hass.async_block_till_done()
    hass.states.async_set("switch.test", "on")
    await hass.async_block_till_done()
    assert item._pending
    announcer.async_stop()
    assert item._pending is None
    callbacks = []
    announcer._watch(item)
    hass.states.async_set("switch.test", "off")
    await hass.async_block_till_done()
    with patch.object(
        announcements,
        "async_call_later",
        side_effect=lambda hass, delay, callback: (callbacks.append(callback), Mock())[1],
    ):
        hass.states.async_set("switch.test", "on")
        await hass.async_block_till_done()
    item.enabled = False
    callbacks[0](None)
    assert not item.last_played
    item.enabled = True
    # Attribute-only updates must not retrigger announcements.
    hass.states.async_set("switch.test", "on", {"friendly_name": "Changed"})
    await hass.async_block_till_done()
    assert not item.last_played
    with patch.object(
        type(hass.services),
        "async_call",
        AsyncMock(side_effect=HomeAssistantError("provider unavailable")),
    ):
        result = await announcer.async_play(item.subentry_id)
    assert not result["ok"] and item.last_error == "provider unavailable"


async def test_switches_can_be_reenabled_and_dnd_cleared(hass, setup_roomcall):
    from custom_components.roomcall.switch import AnnouncementSwitch

    await hass.services.async_call(
        "switch", "turn_on", {"entity_id": "switch.roomcall_do_not_disturb"}, blocking=True
    )
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.roomcall_do_not_disturb"}, blocking=True
    )
    assert not hass.data["roomcall"].dnd
    announcer = hass.data["roomcall"].announcer
    item = Announcement("test", "Test", "scheduled", {"time": "12:00:00"}, enabled=False)
    announcer.items[item.subentry_id] = item
    entry = SimpleNamespace(
        entry_id=setup_roomcall.entry_id,
        subentries={item.subentry_id: SimpleNamespace(title="Test", subentry_type="scheduled")},
    )
    switch = AnnouncementSwitch(entry, announcer, item.subentry_id)
    with patch.object(switch, "async_write_ha_state"):
        await switch.async_turn_on()
    assert item.enabled


async def test_restoration_ignores_incomplete_and_offline_siblings(hass, setup_roomcall, house):
    devices = dr.async_get(hass)
    registry = er.async_get(hass)
    device = devices.async_get_or_create(
        config_entry_id=setup_roomcall.entry_id, identifiers={("test", "receiver")}
    )
    entity = registry.async_get_or_create(
        "media_player", "test", "receiver", device_id=device.id, original_name="Receiver"
    )
    assert restore._identity(hass, entity.entity_id) is None
    devices.async_update_device(
        device.id, manufacturer="Acme", model="Receiver", area_id=house["kitchen"]
    )
    hass.states.async_set(entity.entity_id, "idle")
    other = registry.async_get_or_create(
        "media_player",
        "test",
        "offline_receiver",
        device_id=device.id,
        original_name="Offline Receiver",
    )
    hass.states.async_set(other.entity_id, "unavailable")
    assert restore.related_players(hass, entity.entity_id) == [entity.entity_id]


def load_compatibility_module(name, filename):
    specification = importlib.util.spec_from_file_location(
        f"custom_components.roomcall.{name}", roomcall.__path__[0] + "/" + filename
    )
    module = importlib.util.module_from_spec(specification)
    with patch.dict(sys.modules, {specification.name: module}):
        specification.loader.exec_module(module)
    return module


def test_legacy_llm_platform_import_contract(hass, setup_roomcall):
    original = builtins.__import__

    def legacy_import(name, *args, **kwargs):
        if name == "homeassistant.components.llm":
            raise ImportError("HA predates LLM platform")
        return original(name, *args, **kwargs)

    with patch("builtins.__import__", legacy_import):
        legacy = load_compatibility_module("_legacy_llm", "llm.py")
    tools = legacy.LLMTools(tools=[], prompt="prompt")
    assert tools.tools == [] and tools.prompt == "prompt"


@pytest.mark.parametrize("engine", ["voluptuous", "probatio"])
def test_schema_engine_follows_home_assistant_contract(engine):
    # Both HA schema-engine contracts must be selectable on either supported HA.
    # The native engine is exercised throughout the actual integration tests;
    # this dependency double isolates import selection on the other HA version.
    schema = type("Schema", (), {"__module__": engine + ".schema"})()
    selected = SimpleNamespace(Schema=voluptuous.Schema)
    with (
        patch.dict(sys.modules, {engine: selected}),
        patch(
            "homeassistant.helpers.config_validation.PLATFORM_SCHEMA",
            schema,
        ),
    ):
        module = load_compatibility_module("_legacy_schema", "schema.py")
    assert module.vol is selected
    assert module.vol.Schema({"message": str})({"message": "Hello"}) == {"message": "Hello"}
