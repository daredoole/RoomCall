"""Browser QA with real WebRTC/fake microphones and the documented API harness.

Run with ``python scripts/qa_communication.py`` after installing Playwright Chromium.
Backend authorization, queueing and signaling are separately exercised in tests_ha.
"""

from __future__ import annotations

from ha_screenshots import ORIGIN, OUT, open_card
from playwright.sync_api import expect, sync_playwright
from qa_layout import check_layout


def main() -> None:
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(
            args=[
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--autoplay-policy=no-user-gesture-required",
            ]
        )
        errors = []
        check_layout(browser)
        for width, height in [(390, 844), (1280, 900)]:
            context = browser.new_context(viewport={"width": width, "height": height})
            context.grant_permissions(["microphone"], origin=ORIGIN)
            page = context.new_page()
            page.on("pageerror", lambda error: errors.append(str(error)))
            open_card(page, "mobile" if width < 600 else "desktop", "dark")
            card = page.locator("roomcall-card")
            expect(card.locator(".communication").first).to_be_visible()
            card.locator('[data-mode="type"]').click()
            card.get_by_role("textbox", name="Message", exact=True).fill("Dinner is ready")
            card.get_by_role("button", name="Preview recipients", exact=True).click()
            expect(card.locator(".result")).to_contain_text("Kitchen")
            assert (
                page.evaluate("ROOMCALL_HARNESS.requests.filter(r=>r.operation==='send').length")
                == 0
            )
            card.get_by_role("button", name="Send", exact=True).click()
            expect(card.locator(".result")).to_contain_text("audibility unconfirmed")
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            if width == 390:
                page.screenshot(path=OUT / "ha-card-type.png")
            card.locator('[data-mode="ask"]').click()
            card.get_by_role("button", name="Send", exact=True).click()
            expect(card.locator(".result")).to_contain_text("Pizza")
            if width == 390:
                page.screenshot(path=OUT / "ha-card-ask.png")
            card.locator("summary").click()
            card.get_by_role("button", name="Play test sound", exact=True).first.click()
            card.get_by_role("button", name="I heard it", exact=True).click()
            expect(card.get_by_role("button", name="I heard it", exact=True)).to_have_count(0)
            expect(card.locator(".setup-content")).to_contain_text("I heard it")
            if width == 390:
                page.screenshot(path=OUT / "ha-card-setup.png")
            context.close()

        context = browser.new_context(viewport={"width": 1000, "height": 1600})
        context.grant_permissions(["microphone"], origin=ORIGIN)
        page = context.new_page()
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.add_init_script("""
            window.qaTracks=[];
            const original=navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
            navigator.mediaDevices.getUserMedia=async(...args)=>{
              const stream=await original(...args);
              qaTracks.push(...stream.getTracks());return stream;
            };
        """)
        open_card(page, "mobile", "dark", "&stations=2")
        cards = page.locator("roomcall-card")
        expect(cards.locator(".result")).to_have_count(2)
        for index in (0, 1):
            card = cards.nth(index)
            card.locator("summary").click()
            card.get_by_role("button", name="Enable this station", exact=True).click()
        cards.first.get_by_role("button", name="Refresh", exact=True).click()
        page.wait_for_function("ROOMCALL_HARNESS.stations.size===2")
        cards.first.locator('select[aria-label="Call station"]').select_option(label="Basement")
        cards.first.get_by_role("button", name="Call station", exact=True).click()
        expect(cards.nth(1).get_by_role("button", name="Answer", exact=True)).to_be_visible()
        assert page.evaluate("qaTracks.length") == 1  # Callee mic is still closed.
        cards.nth(1).get_by_role("button", name="Answer", exact=True).click()
        page.wait_for_function(
            "[...document.querySelectorAll('roomcall-card')].every(c=>c._communication.station?.peer?.connectionState==='connected')",
            timeout=20000,
        )
        expect(cards.locator("audio")).to_have_count(2)
        cards.first.get_by_role("button", name="Hang up", exact=True).click()
        page.wait_for_function("qaTracks.every(t=>t.readyState==='ended')")
        assert page.evaluate("ROOMCALL_HARNESS.sessions.size") == 0
        context.close()
        browser.close()
        assert not errors, errors
    print("Desktop/mobile messaging, previews, Q&A, sound tests and consent-based WebRTC passed.")


if __name__ == "__main__":
    main()
