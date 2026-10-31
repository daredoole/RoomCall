"""Regression checks for HA section sizing, fixed grid cells and narrow card columns."""

from __future__ import annotations

import json

from ha_screenshots import open_card
from playwright.sync_api import expect


def add_container(page) -> None:
    """Put the card in a one-column grid followed by another card, like an HA section."""
    page.evaluate("""() => {
          const card=document.querySelector('roomcall-card');
          const container=card.parentElement;
          container.style.cssText='display:grid;grid-template-columns:minmax(0,1fr);gap:12px;min-width:0';
          const following=document.createElement('div');
          following.id='following-card';following.textContent='Modes';
          following.style.cssText='height:120px;background:var(--card-background-color)';
          card.after(following);
          card._rooms.push({area_id:'long',
            name:'A very long room name without any short abbreviation',
            speakers:['media_player.long']});
          card._renderRooms();
          window.layoutContainer=container;
        }""")


def check_layout(browser) -> None:
    for layout in ("simple", "default", "compact", "kiosk"):
        page = browser.new_page(viewport={"width": 688, "height": 560})
        open_card(page, "mobile", "dark", f"&card={layout}")
        card = page.locator("roomcall-card")
        expect(card.locator(".communication").first).to_be_attached()
        add_container(page)
        if layout == "simple":
            # The collapsed card is covered by check_simple; expanded it has every control.
            card.get_by_role("button", name="More", exact=True).click()
        for width in (240, 280, 320, 390, 600):
            page.evaluate("width=>{layoutContainer.style.width=width+'px'}", width)
            # Use the public sizing API just as HA sections does.
            assert card.evaluate("c=>c.getGridOptions().rows") == "auto"
            page.evaluate("layoutContainer.style.gridTemplateRows='auto 120px'")
            page.wait_for_timeout(40)
            assert_contained(page, fixed=False)
            for mode in ("type", "ask", "talk"):
                card.locator(f'[data-mode="{mode}"]').click()
                assert_contained(page, fixed=False)
            card.get_by_role("button", name="Settings", exact=True).click()
            expect(card.get_by_role("dialog", name="Settings", exact=True)).to_be_visible()
            expect(card.locator(".quick-tests")).to_contain_text("Play test sound")
            page.keyboard.press("Escape")
            expect(card.get_by_role("button", name="Settings", exact=True)).to_be_focused()
            # Existing dashboards can still pin rows explicitly. Every control must
            # stay inside that cell, with the bottom reachable by scrolling.
            page.evaluate("layoutContainer.style.gridTemplateRows='280px 120px'")
            page.wait_for_timeout(40)
            assert_contained(page, fixed=True)
            card.get_by_role("button", name="Settings", exact=True).click()
            expect(card.get_by_role("dialog", name="Settings", exact=True)).to_be_visible()
            bounds = card.locator("dialog").bounding_box()
            assert bounds and bounds["x"] >= 0 and bounds["y"] >= 0, bounds
            assert bounds["y"] + bounds["height"] <= 560, bounds
            assert_contained(page, fixed=True)
            page.keyboard.press("Escape")
        page.close()


def check_floors(browser) -> None:
    """A floor chip targets its rooms; a card hiding one of those rooms does not offer it."""
    floor = {
        "id": "floor:downstairs",
        "name": "Downstairs",
        "icon": None,
        "rooms": ["basement", "garage"],
        "speakers": ["Basement", "Garage AirPlay"],
    }
    page = browser.new_page(viewport={"width": 688, "height": 900})
    page.add_init_script(f"window.roomcallFloors = {json.dumps([floor])}")
    open_card(page, "mobile", "dark")
    card = page.locator("roomcall-card")
    chip = card.locator(".rooms .chip", has_text="Downstairs")
    expect(chip).to_have_count(1)
    expect(chip).to_have_attribute("title", "Basement, Garage AirPlay")
    chip.click()
    expect(card.locator(".destination")).to_have_text("To: Downstairs")
    assert page.evaluate("document.querySelector('roomcall-card')._target") == floor["id"]
    # History shows the floor's name, and a floor outside this house still reads cleanly.
    assert page.evaluate(
        """() => { const c = document.querySelector('roomcall-card');
        return [c._roomName('floor:downstairs'), c._roomName('floor:gone')]; }"""
    ) == ["Downstairs", "gone"]
    page.evaluate(
        """async () => { const c = document.querySelector('roomcall-card');
        c._config = { ...c._config, rooms: ['basement'] };
        await c._load(); c._renderRooms(); }"""
    )
    expect(card.locator(".rooms .chip", has_text="Downstairs")).to_have_count(0)
    page.close()


def check_simple(browser) -> None:
    """Simple shows rooms, talk and the latest call; More reveals and Less hides the rest."""
    hidden_when_collapsed = (".toggles", ".people", "[data-toolbar]")
    for appearance in ("glass", "material", "home-assistant"):
        page = browser.new_page(viewport={"width": 688, "height": 560})
        open_card(page, "mobile", "dark", f"&card=simple&appearance={appearance}")
        card = page.locator("roomcall-card")
        expect(card.locator(".communication").first).to_be_attached()
        expect(card.locator(".history .call")).to_have_count(1)
        add_container(page)
        more = card.get_by_role("button", name="More", exact=True)
        assert more.get_attribute("aria-expanded") == "false"
        for selector in hidden_when_collapsed:
            expect(card.locator(selector).first).to_be_hidden()
        expect(card.get_by_role("button", name="Settings", exact=True)).to_be_hidden()
        expect(card.locator(".rooms .chip").first).to_be_visible()
        expect(card.locator("button.talk")).to_be_visible()
        expect(card.locator(".destination")).to_have_text("To: Kitchen")
        expect(card.locator(".history .call")).to_have_count(1)
        expect(card.locator(".history")).to_contain_text("Dinner's ready")
        for width in (240, 280, 320, 390, 600):
            page.evaluate("width=>{layoutContainer.style.width=width+'px'}", width)
            page.evaluate("layoutContainer.style.gridTemplateRows='auto 120px'")
            page.wait_for_timeout(40)
            assert_contained(page, fixed=False)
            bounds = more.bounding_box()
            assert bounds and bounds["width"] >= 44 and bounds["height"] >= 44, bounds
            page.evaluate("layoutContainer.style.gridTemplateRows='280px 120px'")
            page.wait_for_timeout(40)
            assert_contained(page, fixed=True)
        page.evaluate("layoutContainer.style.width='390px'")
        page.evaluate("layoutContainer.style.gridTemplateRows='auto 120px'")
        more.click()
        less = card.get_by_role("button", name="Less", exact=True)
        assert less.get_attribute("aria-expanded") == "true"
        for selector in hidden_when_collapsed:
            expect(card.locator(selector).first).to_be_visible()
        expect(card.locator(".history .call")).to_have_count(3)
        card.locator('[data-mode="type"]').click()
        expect(card.locator("button.talk")).to_be_hidden()
        page.wait_for_timeout(40)
        assert_contained(page, fixed=False)
        # Collapsing returns to Talk: the hidden toolbar can't leave the card in Type.
        less.click()
        expect(card.locator("button.talk")).to_be_visible()
        expect(card.get_by_role("textbox", name="Message", exact=True)).to_be_hidden()
        assert card.locator('[data-mode="talk"]').get_attribute("aria-pressed") == "true"
        expect(card.locator(".history .call")).to_have_count(1)
        assert more.get_attribute("aria-expanded") == "false"
        page.close()
    # Turning recent calls off later must not let More reveal calls fetched before.
    page = browser.new_page(viewport={"width": 390, "height": 820})
    open_card(page, "mobile", "dark", "&card=simple")
    card = page.locator("roomcall-card")
    expect(card.locator(".history .call")).to_have_count(1)
    card.evaluate("c=>c.setConfig({...c._config, history: 0})")
    expect(card.locator(".communication").first).to_be_attached()
    card.get_by_role("button", name="More", exact=True).click()
    expect(card.locator("[data-toolbar]")).to_be_visible()
    expect(card.locator(".history .call")).to_have_count(0)
    page.close()
    # A saved card without `layout` keeps the Standard layout, with no More button.
    page = browser.new_page(viewport={"width": 390, "height": 820})
    open_card(page, "mobile", "dark")
    card = page.locator("roomcall-card")
    assert card.get_attribute("layout") == "default"
    expect(card.locator(".communication").first).to_be_attached()
    expect(card.locator(".more")).to_be_hidden()
    expect(card.locator("[data-toolbar]")).to_be_visible()
    expect(card.locator(".history .call")).to_have_count(3)
    page.close()


def check_appearances(browser) -> None:
    """Glass, Material and HA-theme looks: tokens, theme switching, contrast, no overflow."""
    cases = [
        ("glass", "dark", "", "rgb(10, 132, 255)"),
        ("material", "light", "&color=00a389", None),
        ("glass", "light", "&color=ffd60a", "rgb(255, 214, 10)"),
        ("home-assistant", "dark", "", None),
    ]
    for appearance, scheme, extra, primary in cases:
        page = browser.new_page(viewport={"width": 390, "height": 820})
        open_card(page, "mobile", scheme, f"&appearance={appearance}{extra}")
        card = page.locator("roomcall-card")
        assert card.get_attribute("appearance") == appearance
        assert (card.get_attribute("dark") is not None) == (scheme == "dark")
        talk = card.locator("button.talk")
        if primary:
            assert talk.evaluate("e=>getComputedStyle(e).backgroundColor") == primary
        if extra.endswith("ffd60a"):
            # A light colour gets dark text so the button label stays readable.
            assert talk.evaluate("e=>getComputedStyle(e).color") == "rgb(16, 19, 23)"
        if appearance == "home-assistant":
            assert card.evaluate("e=>e.style.getPropertyValue('--primary-color')") == ""
        for width in (240, 600):
            card.evaluate("(e,w)=>{e.parentElement.style.width=w+'px'}", width)
            page.wait_for_timeout(40)
            assert card.evaluate(
                "e=>{const c=e.shadowRoot.querySelector('ha-card');"
                "return c.scrollWidth<=c.clientWidth+1}"
            ), (appearance, width)
        # "auto" follows Home Assistant's dark mode as it changes.
        page.evaluate("""() => {const c=document.querySelector('roomcall-card');
          c.hass={...c._hass,themes:{darkMode:!c._hass.themes.darkMode}}}""")
        assert (card.get_attribute("dark") is None) == (scheme == "dark")
        page.close()


def check_large_text(browser) -> None:
    for width in (240, 390, 900):
        page = browser.new_page(viewport={"width": width, "height": 820})
        open_card(page, "mobile", "dark")
        page.add_style_tag(content="html { font-size:200% !important; }")
        card = page.locator("roomcall-card")
        assert card.evaluate("e=>e.scrollWidth<=e.clientWidth")
        for button in card.locator("[data-toolbar] button").all():
            bounds = button.bounding_box()
            assert bounds and bounds["width"] >= 44 and bounds["height"] >= 44, bounds
        card.get_by_role("button", name="Settings", exact=True).click()
        dialog = card.get_by_role("dialog", name="Settings", exact=True)
        expect(dialog).to_be_visible()
        assert dialog.evaluate("e=>e.scrollWidth<=e.clientWidth")
        setup = dialog.get_by_role("tab", name="Setup and sound tests", exact=True)
        setup.focus()
        page.keyboard.press("ArrowRight")
        expect(dialog.get_by_role("tab", name="Siri Shortcuts", exact=True)).to_be_focused()
        page.keyboard.press("Home")
        expect(setup).to_be_focused()
        page.keyboard.press("Escape")
        expect(card.get_by_role("button", name="Settings", exact=True)).to_be_focused()
        page.close()


def assert_contained(page, *, fixed: bool) -> None:
    dimensions = page.evaluate("""() => {
      const host=document.querySelector('roomcall-card');
      const card=host.shadowRoot.querySelector('ha-card');
      const following=document.querySelector('#following-card');
      const bounds=card.getBoundingClientRect();
      return {width:card.clientWidth,scrollWidth:card.scrollWidth,
        height:card.clientHeight,scrollHeight:card.scrollHeight,
        bottom:bounds.bottom,next:following.getBoundingClientRect().top,
        hostBottom:host.getBoundingClientRect().bottom,
        size:host.getCardSize(),overflow:getComputedStyle(card).overflowY};
    }""")
    assert dimensions["scrollWidth"] <= dimensions["width"] + 1, dimensions
    assert dimensions["bottom"] <= dimensions["next"] - 10, dimensions
    assert abs(dimensions["hostBottom"] - dimensions["bottom"]) <= 1, dimensions
    if fixed:
        assert dimensions["height"] <= 280, dimensions
        assert dimensions["overflow"] == "auto", dimensions
    else:
        assert dimensions["scrollHeight"] <= dimensions["height"] + 1, dimensions
        assert dimensions["size"] * 50 >= dimensions["height"], dimensions
