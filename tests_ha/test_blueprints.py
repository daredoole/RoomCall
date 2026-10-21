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

BLUEPRINTS = sorted(
    (Path(__file__).resolve().parents[1] / "blueprints" / "automation" / "roomcall").glob("*.yaml")
)
SAMPLE_INPUTS = {
    "doorbell.yaml": {"doorbell": "binary_sensor.front_doorbell", "rooms": ["kitchen"]},
    "appliance_done.yaml": {"power_sensor": "sensor.washer_power"},
    "bedtime_dnd.yaml": {},
    "arrival.yaml": {"people": ["person.alex"]},
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
