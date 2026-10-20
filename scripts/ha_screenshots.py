"""Capture documentation screenshots of the Home Assistant push-to-talk card.

The card is rendered by ``scripts/ha_card_harness.html`` with a fake ``hass`` object, so no
Home Assistant instance or speakers are needed.

Usage:  python scripts/ha_screenshots.py
Needs:  pip install playwright && playwright install chromium
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, Route, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets"
ORIGIN = "http://localhost:8123"
FILES = {
    "/harness.html": (ROOT / "scripts" / "ha_card_harness.html", "text/html"),
    "/roomcall-card.js": (
        ROOT / "custom_components" / "roomcall" / "frontend" / "roomcall-card.js",
        "text/javascript",
    ),
    "/logo.svg": (OUT / "logo.svg", "image/svg+xml"),
}


def serve(route: Route) -> None:
    path = "/" + route.request.url.removeprefix(ORIGIN).split("?", 1)[0].lstrip("/")
    if path not in FILES:
        route.fulfill(status=404)
        return
    file, content_type = FILES[path]
    route.fulfill(body=file.read_bytes(), content_type=content_type)


def open_card(page: Page, layout: str, scheme: str, extra: str = "") -> None:
    page.route(f"{ORIGIN}/**", serve)
    page.goto(f"{ORIGIN}/harness.html?layout={layout}&scheme={scheme}{extra}")
    page.wait_for_function(
        "document.querySelector('roomcall-card')?.shadowRoot"
        "?.querySelectorAll('.rooms .chip').length > 3"
    )
    page.wait_for_timeout(700)  # icon font


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
        )
        phone = {"width": 390, "height": 844}
        shots = [
            ("ha-card-mobile.png", phone, "mobile", "dark", ""),
            ("ha-card-mobile-light.png", phone, "mobile", "light", ""),
            ("ha-card-reply.png", phone, "mobile", "dark", "&roomcall=basement"),
            ("ha-card-dnd.png", phone, "mobile", "dark", "&dnd=on"),
            (
                "ha-card-compact.png",
                {"width": 390, "height": 420},
                "mobile",
                "dark",
                "&card=compact",
            ),
            ("ha-card-kiosk.png", {"width": 800, "height": 1180}, "mobile", "dark", "&card=kiosk"),
            ("ha-dashboard-desktop.png", {"width": 1280, "height": 760}, "desktop", "dark", ""),
        ]
        for name, viewport, layout, scheme, extra in shots:
            page = browser.new_page(viewport=viewport, device_scale_factor=2)
            open_card(page, layout, scheme, extra)
            page.screenshot(path=OUT / name)
            page.close()

        page = browser.new_page(viewport=phone, device_scale_factor=2)
        page.context.grant_permissions(["microphone"], origin=ORIGIN)
        open_card(page, "mobile", "dark")
        talk = page.locator("roomcall-card").locator("button.talk")
        talk.hover()
        page.mouse.down()
        page.wait_for_timeout(1500)
        page.screenshot(path=OUT / "ha-card-talking.png")
        page.mouse.up()
        page.locator("roomcall-card").locator(".status.ok").wait_for(timeout=10000)
        page.screenshot(path=OUT / "ha-card-delivered.png")
        page.close()

        page = browser.new_page(viewport={"width": 512, "height": 512})
        page.route(f"{ORIGIN}/**", serve)
        page.goto(f"{ORIGIN}/logo.svg")
        page.screenshot(path=OUT / "logo.png", omit_background=True)
        browser.close()
    print(f"Screenshots written to {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
