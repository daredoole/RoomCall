"""Frame real iOS Simulator screenshots in an iPhone outline for the README and docs.

The screenshots come unmodified from the ``testScreenshot*`` UI tests, which CI exports from
the result bundle (artifact ``roomcall-ios-simulator`` → ``ios-screenshots/``). Only the frame
is drawn here; the screen is the simulator's own pixels.

Usage:  python scripts/ios_mockups.py <exported ios-screenshots directory>
Needs:  pip install playwright && playwright install chromium
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "assets"
SCREENS = ("sign-in", "rooms", "talking", "message", "tasks", "history", "settings", "doorbell")
NAMES = [f"ios-{screen}-{look}" for screen in SCREENS for look in ("light", "dark")]

FRAME = """<!doctype html><html><head><style>
  html,body { margin:0; background:transparent; }
  .phone { position:relative; width:{w}px; padding:{bezel}px; border-radius:{outer}px;
    background:linear-gradient(145deg,#3a3a3c,#1c1c1e 45%,#2c2c2e); box-sizing:content-box;
    box-shadow:0 0 0 2px #4a4a4c inset, 0 30px 60px rgb(0 0 0 / .28); margin:24px; }
  .screen { display:block; width:{w}px; border-radius:{inner}px; }
  .island { position:absolute; top:{island_top}px; left:50%; transform:translateX(-50%);
    width:{island_w}px; height:{island_h}px; border-radius:{island_h}px; background:#000; }
  .button { position:absolute; width:6px; border-radius:3px; background:#2c2c2e; }
</style></head><body>
<div class="phone">
  <img class="screen" src="data:image/png;base64,{png}">
  <div class="island"></div>
  <div class="button" style="left:-4px;top:{b1}px;height:{bh1}px"></div>
  <div class="button" style="left:-4px;top:{b2}px;height:{bh2}px"></div>
  <div class="button" style="left:-4px;top:{b3}px;height:{bh2}px"></div>
  <div class="button" style="right:-4px;top:{b2}px;height:{bh3}px"></div>
</div></body></html>"""


def exported(directory: Path) -> dict[str, Path]:
    """Map attachment names (``ios-rooms-dark``) to exported files via the manifest."""
    found: dict[str, Path] = {}
    for test in json.loads((directory / "manifest.json").read_text()):
        for item in test.get("attachments", []):
            name = item.get("suggestedHumanReadableName", "").split("_0_")[0]
            if name in NAMES:
                found[name] = directory / item["exportedFileName"]
    return found


def frame(page, screenshot: Path, output: Path, width: int = 300) -> None:
    scale = width / 1206  # the simulator capture is 1206 px wide at 3x
    values = {
        "w": width,
        "bezel": round(42 * scale),
        "outer": round(190 * scale),
        "inner": round(150 * scale),
        "island_top": round(42 * scale + 33 * scale),
        "island_w": round(375 * scale),
        "island_h": round(110 * scale),
        "b1": round(560 * scale),
        "bh1": round(100 * scale),
        "b2": round(760 * scale),
        "bh2": round(200 * scale),
        "b3": round(1010 * scale),
        "bh3": round(300 * scale),
        "png": base64.b64encode(screenshot.read_bytes()).decode(),
    }
    html = FRAME
    for key, value in values.items():
        html = html.replace("{" + key + "}", str(value))
    page.set_content(html)
    page.wait_for_load_state("networkidle")
    page.locator(".phone").screenshot(path=output, omit_background=True)


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    shots = exported(Path(sys.argv[1]))
    missing = sorted(set(NAMES) - set(shots))
    if missing:
        print("Missing screenshots:", ", ".join(missing))
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(device_scale_factor=2)
        for name, path in sorted(shots.items()):
            frame(page, path, OUT / f"{name}.png")
        browser.close()
    print(f"Framed {len(shots)} screenshots into {OUT}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
