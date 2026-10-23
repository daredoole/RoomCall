"""The card's Glass/Material looks and its translated editor, run from the shipped JavaScript."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from custom_components.roomcall.localization import catalog

CARD = Path(__file__).parents[1] / "custom_components/roomcall/frontend/roomcall-card.js"
NODE = shutil.which("node")
SOURCE = CARD.read_text()


def _run(expression: str):
    """Evaluate the card's theme and editor helpers (no DOM needed) and return JSON."""
    if not NODE:
        pytest.skip("node is not installed")
    head = SOURCE[: SOURCE.index("class RoomCallCard extends HTMLElement")]
    head = head[head.index("const LAYOUTS") :]
    editor = SOURCE[SOURCE.index("const EDITOR_TEXT") : SOURCE.index("class RoomCallCardEditor")]
    script = f"{head}\n{editor}\nprocess.stdout.write(JSON.stringify({expression}));"
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_glass_tokens_follow_the_chosen_colour_and_mode():
    dark, light = _run(
        '[themeTokens("glass", true, "#0a84ff"), themeTokens("glass", false, "#0a84ff")]'
    )
    assert dark["--primary-color"] == light["--primary-color"] == "#0a84ff"
    assert dark["--rc-bg"] != light["--rc-bg"]
    assert dark["--primary-text-color"] != light["--primary-text-color"]
    assert "blur(" in dark["--rc-blur"]
    # The editor's colour picker gives [r, g, b]; YAML gives "#hex". Both mean the same.
    assert _run('themeTokens("glass", true, [10, 132, 255])') == dark


def test_light_colours_get_dark_text():
    assert _run('onSeed("#ffd60a")') == "#101317"
    assert _run('onSeed("#0a84ff")') == "#ffffff"
    tokens = _run('themeTokens("glass", false, "#ffd60a")')
    assert tokens["--text-primary-color"] == tokens["--rc-on-selected"] == "#101317"


def test_material_builds_a_tonal_palette_from_one_colour():
    teal, purple = _run(
        '[themeTokens("material", false, "#00a389"), themeTokens("material", false, "#7c4dff")]'
    )
    assert teal["--primary-color"] != purple["--primary-color"]
    assert teal["--rc-blur"] == "none"
    assert "linear-gradient" in teal["--rc-orb"]
    dark = _run('themeTokens("material", true, "#00a389")')
    # Dark mode lightens the primary tone (Material's tone 70+), light mode darkens it.
    assert int(re.search(r"(\d+)% /", dark["--primary-color"]).group(1)) >= 70
    assert int(re.search(r"(\d+)% /", teal["--primary-color"]).group(1)) <= 45


def test_home_assistant_appearance_keeps_the_dashboard_theme():
    assert _run('themeTokens("home-assistant", true, "#ff0000")') == {}


def test_each_appearance_has_its_icon_set():
    icons = _run("ICONS")
    assert set(icons) == set(_run("APPEARANCES"))
    assert icons["glass"]["talk"] != icons["material"]["talk"]
    assert all(set(found) == set(icons["glass"]) for found in icons.values())


def test_editor_offers_every_option_and_text_is_translated():
    schema = _run("editorSchema((key) => key)")
    flat = [field for item in schema for field in item.get("schema", [item])]
    options = {
        field["name"]: [o["value"] for o in field["selector"]["select"]["options"]]
        for field in flat
        if "select" in field.get("selector", {})
    }
    assert options == {
        "layout": ["default", "compact", "kiosk"],
        "appearance": ["glass", "material", "home-assistant"],
        "theme": ["auto", "light", "dark"],
    }
    assert any(field["name"] == "color" for field in flat)
    keys = _run("Object.keys(EDITOR_TEXT)")
    labels = {f"{name}_{v.replace('-', '_')}" for name, values in options.items() for v in values}
    assert labels <= set(keys)
    english = catalog("en")
    for language in ("es", "de", "fr", "nl"):
        strings = catalog(language)
        assert all(strings.get(f"editor_{key}") for key in keys), language
    assert _run("EDITOR_TEXT") == {key: english[f"editor_{key}"] for key in keys}
    assert catalog("de")["editor_appearance"] != english["editor_appearance"]


async def test_locale_api_serves_editor_text(hass, setup_roomcall, hass_client):
    client = await hass_client()
    strings = await (await client.get("/api/roomcall/locale?language=fr")).json()
    assert strings["editor_theme_dark"] == "Sombre"
