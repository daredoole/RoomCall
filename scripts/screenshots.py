"""Capture documentation screenshots of the web app running in --demo mode.

Usage:  python scripts/screenshots.py
Needs:  pip install playwright && playwright install chromium
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def wait_for(port: int, timeout: float = 15) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with socket.socket() as s:
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        time.sleep(0.2)
    raise TimeoutError("server did not start")


def main() -> int:
    port = free_port()
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "roomcall.cli",
            "serve",
            "--demo",
            "--port",
            str(port),
            "-c",
            "/nonexistent",
        ],
        cwd=ROOT,
    )
    try:
        wait_for(port)
        OUT.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            browser = p.chromium.launch(
                args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
            )
            for name, viewport, scheme in [
                ("screenshot-mobile.png", {"width": 390, "height": 760}, "dark"),
                ("screenshot-mobile-light.png", {"width": 390, "height": 760}, "light"),
                ("screenshot-desktop.png", {"width": 1100, "height": 720}, "dark"),
            ]:
                page = browser.new_page(
                    viewport=viewport, color_scheme=scheme, device_scale_factor=2
                )
                page.goto(f"http://127.0.0.1:{port}/")
                page.wait_for_selector(".room:nth-child(3)")
                page.locator(".room", has_text="Kitchen").click()
                page.screenshot(path=OUT / name)
                page.close()

            # Live "talking" state plus a delivery log entry.
            page = browser.new_page(
                viewport={"width": 390, "height": 760}, color_scheme="dark", device_scale_factor=2
            )
            page.context.grant_permissions(["microphone"])
            page.goto(f"http://127.0.0.1:{port}/")
            page.wait_for_selector(".room:nth-child(3)")
            talk = page.locator("#talk")
            talk.hover()
            page.mouse.down()
            page.wait_for_timeout(1200)
            page.screenshot(path=OUT / "screenshot-talking.png")
            page.mouse.up()
            page.wait_for_selector(".log div", timeout=15000)
            page.screenshot(path=OUT / "screenshot-delivered.png")
            browser.close()
        print(f"Screenshots written to {OUT}")
        return 0
    finally:
        server.terminate()
        server.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
