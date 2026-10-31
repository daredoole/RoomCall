"""Every blueprint in the repo loads, and the automation it makes is valid."""

from __future__ import annotations

from pathlib import Path

import pytest
from homeassistant.components.automation.config import (
    AUTOMATION_BLUEPRINT_SCHEMA,
    async_validate_config_item,
)
from homeassistant.components.blueprint.models import Blueprint, BlueprintInputs
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util.yaml import load_yaml_dict
from pytest_homeassistant_custom_component.common import async_mock_service

BLUEPRINTS = sorted(
    (Path(__file__).resolve().parents[1] / "blueprints" / "automation" / "roomcall").glob("*.yaml")
)
SAMPLE_INPUTS = {
    "doorbell.yaml": {"doorbell": "binary_sensor.front_doorbell", "rooms": ["kitchen"]},
    "appliance_done.yaml": {"power_sensor": "sensor.washer_power"},
    "bedtime_dnd.yaml": {},
    "arrival.yaml": {"people": ["person.alex"]},
    "button_message.yaml": {"button": "event.hall_button", "rooms": ["kitchen"]},
}


def test_every_blueprint_has_sample_inputs():
    assert {p.name for p in BLUEPRINTS} == set(SAMPLE_INPUTS)


@pytest.mark.parametrize("path", BLUEPRINTS, ids=lambda p: p.name)
async def test_blueprint_is_valid(hass: HomeAssistant, path: Path):
    await async_setup_component(hass, "automation", {})
    blueprint = Blueprint(
        load_yaml_dict(str(path)), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
    )
    assert blueprint.metadata["source_url"].endswith(path.name)
    inputs = BlueprintInputs(
        blueprint, {"use_blueprint": {"path": path.name, "input": SAMPLE_INPUTS[path.name]}}
    )
    inputs.validate()
    config = inputs.async_substitute()
    config["id"] = path.stem
    validated = await async_validate_config_item(hass, path.stem, config)
    assert validated is not None
    assert validated.validation_error is None, validated.validation_error


async def _button_automation(hass: HomeAssistant, entity_id: str, **inputs) -> list:
    path = next(p for p in BLUEPRINTS if p.name == "button_message.yaml")
    blueprint = Blueprint(
        load_yaml_dict(str(path)), expected_domain="automation", schema=AUTOMATION_BLUEPRINT_SCHEMA
    )
    config = BlueprintInputs(
        blueprint,
        {
            "use_blueprint": {
                "path": path.name,
                "input": {"button": entity_id, "cooldown": 0, "message": "Hello", **inputs},
            }
        },
    ).async_substitute()
    calls = async_mock_service(hass, "roomcall", "broadcast")
    hass.states.async_set(entity_id, "off" if entity_id.startswith("binary") else "2026-01-01")
    assert await async_setup_component(hass, "automation", {"automation": [config]})
    await hass.async_block_till_done()
    return calls


async def test_button_message_on_any_press_and_only_the_chosen_press(hass: HomeAssistant):
    calls = await _button_automation(hass, "event.hall", press_type="double", rooms=["kitchen"])
    hass.states.async_set("event.hall", "2026-01-02", {"event_type": "single"})
    await hass.async_block_till_done()
    assert calls == []
    hass.states.async_set("event.hall", "2026-01-03", {"event_type": "double"})
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["areas"] == ["kitchen"]
    assert calls[0].data["message"] == "Hello"
    assert calls[0].data["priority"] == "normal"


async def test_button_message_ignores_startup_and_release(hass: HomeAssistant):
    calls = await _button_automation(hass, "binary_sensor.hall", urgent=True)
    hass.states.async_set("binary_sensor.hall", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.hall", "on")
    await hass.async_block_till_done()
    assert calls == []  # coming back from unavailable is not a press
    hass.states.async_set("binary_sensor.hall", "off")
    await hass.async_block_till_done()
    assert calls == []
    hass.states.async_set("binary_sensor.hall", "on")
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["priority"] == "urgent"


async def test_button_message_ignores_attribute_updates(hass: HomeAssistant):
    calls = await _button_automation(hass, "event.hall")
    hass.states.async_set("event.hall", "2026-01-01", {"battery": 80})
    await hass.async_block_till_done()
    assert calls == []


async def test_button_message_counts_the_first_press_of_a_new_button(hass: HomeAssistant):
    calls = await _button_automation(hass, "button.hall")
    hass.states.async_set("button.hall", "unknown")
    await hass.async_block_till_done()
    hass.states.async_set("button.hall", "2026-01-02")
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_button_message_ignores_a_binary_sensor_leaving_unknown(hass: HomeAssistant):
    calls = await _button_automation(hass, "binary_sensor.hall")
    hass.states.async_set("binary_sensor.hall", "unknown")
    await hass.async_block_till_done()
    hass.states.async_set("binary_sensor.hall", "on")
    await hass.async_block_till_done()
    assert calls == []
