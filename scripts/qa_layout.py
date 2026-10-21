"""Regression checks for HA section sizing, fixed grid cells and narrow card columns."""

from __future__ import annotations

from ha_screenshots import open_card
from playwright.sync_api import expect


def check_layout(browser) -> None:
    for layout in ("default", "compact", "kiosk"):
        page = browser.new_page(viewport={"width": 688, "height": 560})
        open_card(page, "mobile", "dark", f"&card={layout}")
        card = page.locator("roomcall-card")
        expect(card.locator(".communication").first).to_be_attached()
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
        for width in (240, 280, 320, 390, 600):
            page.evaluate("width=>{layoutContainer.style.width=width+'px'}", width)
            # Use the public sizing API just as HA sections does.
            assert card.evaluate("c=>c.getGridOptions().rows") == "auto"
            page.evaluate("layoutContainer.style.gridTemplateRows='auto 120px'")
            page.wait_for_timeout(40)
            assert_contained(page, fixed=False)
            if layout != "compact":
                for mode in ("type", "ask", "talk"):
                    card.locator(f'[data-mode="{mode}"]').click()
                    assert_contained(page, fixed=False)
                card.locator("summary").click()
                expect(card.locator(".setup-content")).to_contain_text("Play test sound")
                assert_contained(page, fixed=False)
                card.locator("summary").click()
            # Existing dashboards can still pin rows explicitly. Every control must
            # stay inside that cell, with the bottom reachable by scrolling.
            page.evaluate("layoutContainer.style.gridTemplateRows='280px 120px'")
            page.wait_for_timeout(40)
            assert_contained(page, fixed=True)
            if layout != "compact":
                card.locator("summary").click()
                expect(card.locator(".setup-content")).to_contain_text("Play test sound")
                # Opening setup refreshes/replaces its controls asynchronously.
                # Resolve the current bottom control on every retry.
                page.wait_for_function("""() => {
                  const root=document.querySelector('roomcall-card').shadowRoot;
                  const buttons=root.querySelectorAll('.setup-content button');
                  const button=buttons[buttons.length-1];
                  button.scrollIntoView({block:'nearest'});
                  const b=button.getBoundingClientRect();
                  const c=root.querySelector('ha-card').getBoundingClientRect();
                  return b.top>=Math.max(0,c.top) && b.bottom<=Math.min(innerHeight,c.bottom);
                }""")
                assert_contained(page, fixed=True)
                card.locator("summary").click()
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
