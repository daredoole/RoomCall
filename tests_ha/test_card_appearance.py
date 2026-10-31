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


def _run_source(expression: str, start: str, end: str):
    """Evaluate one slice of the card source."""
    if not NODE:
        pytest.skip("node is not installed")
    part = SOURCE[SOURCE.index(start) : SOURCE.index(end)]
    script = f"{part}\nprocess.stdout.write(JSON.stringify({expression}));"
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
        "layout": ["simple", "default", "compact", "kiosk"],
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


def test_new_cards_start_simple_and_saved_cards_keep_their_layout():
    stub, saved, chosen = _run(
        "[cardConfig(NEW_CARD), cardConfig({type: 'custom:roomcall-card', title: 'Hall'}),"
        " LAYOUTS.map((layout) => cardConfig({layout}).layout)]"
    )
    # The card picker's starting config is the simple layout.
    assert "static getStubConfig() {\n    return { ...NEW_CARD };" in SOURCE
    assert stub["layout"] == "simple"
    assert stub["title"] == "Intercom"
    # A card saved before the simple layout existed has no `layout` and stays Standard.
    assert saved["layout"] == "default"
    assert saved["title"] == "Hall"
    assert {key: saved[key] for key in ("history", "people", "live", "appearance", "theme")} == {
        "history": 3,
        "people": True,
        "live": True,
        "appearance": "glass",
        "theme": "auto",
    }
    assert chosen == _run("LAYOUTS")


def test_simple_layout_toggle_is_translated():
    for language in ("en", "es", "de", "fr", "nl"):
        strings = catalog(language)
        assert strings["more_options"] and strings["fewer_options"], language
        assert strings["more_options"] != strings["fewer_options"], language
    assert catalog("de")["more_options"] == "Mehr"


async def test_locale_api_serves_editor_text(hass, setup_roomcall, hass_client):
    client = await hass_client()
    strings = await (await client.get("/api/roomcall/locale?language=fr")).json()
    assert strings["editor_theme_dark"] == "Sombre"


def test_tap_text_is_translated():
    text = _run_source("TAP_TEXT", "const TAP_TEXT", "const cardConfig")
    english = catalog("en")
    assert text == {key: english[key] for key in text}
    for language in ("es", "de", "fr", "nl"):
        strings = catalog(language)
        assert all(strings.get(key) and strings[key] != text[key] for key in text), language


def _run_tap(body: str):
    """Run the card's tap-mode methods on a stub, with timers recorded rather than scheduled."""
    limit = SOURCE[SOURCE.index("const TAP_LIMIT_MS") : SOURCE.index("const cardConfig")]
    methods = SOURCE[SOURCE.index("  _tapText(key) {") : SOURCE.index("  async _start(e) {")]
    script = f"""
    {limit}
    const timers = [];
    const cleared = [];
    globalThis.setTimeout = (run, delay) => {{ timers.push({{ run, delay }}); return timers.length; }};
    globalThis.clearTimeout = (id) => cleared.push(id);
    const element = () => ({{
      attrs: {{}}, gone: [], textContent: "", classList: {{ remove(name) {{ element.removed = name; }} }},
      setAttribute(name, value) {{ this.attrs[name] = value; }},
      removeAttribute(name) {{ delete this.attrs[name]; }},
    }});
    const label = element();
    class Stub {{
      constructor(config) {{
        this._config = config; this.starts = 0; this.stops = 0;
        this.shadowRoot = {{ querySelector: () => label }};
      }}
      _start() {{ this.starts += 1; }}
      _stop() {{ this.stops += 1; }}
{methods}
    }}
    const card = new Stub({{ tap_to_talk: true }});
    const talk = element();
    {body}
    """
    if not NODE:
        pytest.skip("node is not installed")
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(result.stdout)


def test_tap_toggles_and_never_doubles_a_finishing_call():
    assert _run_tap(
        """
        const seen = [];
        const count = () => seen.push([card.starts, card.stops]);
        card._toggle(); count();
        card._live = { done: false }; card._toggle(); count();
        card._live = { done: true }; card._toggle(); count();
        card._live = null; card._starting = true; card._toggle(); count();
        card._starting = false; card._recorder = {}; card._toggle(); count();
        process.stdout.write(JSON.stringify(seen));
        """
    ) == [[1, 0], [1, 1], [1, 1], [1, 2], [1, 3]]


def test_tapped_microphone_closes_itself_after_two_minutes():
    delay, stops_before, stops_after, pressed, closed, cleared_ok = _run_tap(
        """
        card._talkOpened(talk);
        const delay = timers[0].delay;
        const pressed = talk.attrs["aria-pressed"];
        const before = card.stops;
        timers[0].run();
        const after = card.stops;
        card._talkClosed(talk);
        process.stdout.write(JSON.stringify(
          [delay, before, after, pressed, talk.attrs["aria-pressed"] ?? null, cleared.length === 1]));
        """
    )
    assert delay == 120_000
    assert (stops_before, stops_after) == (0, 1)
    assert pressed == "true"
    assert closed is None
    assert cleared_ok


def test_hold_mode_sets_no_timer_and_keeps_its_label():
    assert _run_tap(
        """
        const hold = new Stub({ tap_to_talk: false });
        hold._talkOpened(talk);
        process.stdout.write(JSON.stringify([timers.length, hold._idleLabel(), card._idleLabel()]));
        """
    ) == [0, "Hold to talk", "Tap to talk"]
