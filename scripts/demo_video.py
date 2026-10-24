"""Record the card's parts of the demo video and assemble it with the owner's real footage.

The card scenes are recorded from the real card in ``scripts/ha_card_harness.html`` (sample data,
simulated delivery). Speaker scenes must come from real footage: without ``--footage`` the script
only builds a silent preview with labelled placeholders, never a finished video.

Usage:  python scripts/demo_video.py                      # scenes + build/demo/demo-preview.mp4
        python scripts/demo_video.py --footage DIR        # + docs/assets/demo.mp4 and poster
Needs:  pip install playwright && playwright install chromium; ffmpeg with libass/freetype
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import tempfile
from pathlib import Path

from ha_screenshots import ORIGIN, open_card
from playwright.sync_api import Browser, Page, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build" / "demo"
ASSETS = ROOT / "docs" / "assets"
WIDTH, HEIGHT, FPS = 1080, 1920, 30
ZOOM = 2.5  # a 432x768 phone layout drawn at 1080x1920; screencasts ignore device scale
FONT = "/usr/share/fonts/rsms-inter-fonts/Inter-SemiBold.ttf"

# (file, caption, source). "auto" scenes are recorded here; the others are owner footage.
SCENES = [
    ("01-pick", "Pick a room", "auto"),
    ("02-talk", "Hold to talk", "auto"),
    ("03-sonos", "On your Sonos, over the music", "footage"),
    ("04-notification", "Reply from the notification", "footage"),
    ("05-reply", "…straight back to the kitchen", "auto"),
    ("06-siri", "Hey Siri, to any speaker", "footage"),
    ("07-doorbell", "Answer the door from any room", "auto"),
    ("08-end", "", "auto"),
]

# A visible touch point (recorded video has no cursor), and the phone layout at full size.
TOUCH = """
addEventListener('DOMContentLoaded', () => {
  document.documentElement.style.zoom = '__ZOOM__';
  const dot = document.createElement('div');
  dot.style.cssText = 'position:fixed;width:44px;height:44px;margin:-22px 0 0 -22px;' +
    'border-radius:50%;background:rgba(255,255,255,.55);border:2px solid #fff;' +
    'pointer-events:none;z-index:99999;opacity:0;transition:opacity .15s,transform .15s';
  document.body.append(dot);
  addEventListener('pointerdown', (e) => {
    dot.style.left = e.clientX + 'px'; dot.style.top = e.clientY + 'px';
    dot.style.opacity = 1; dot.style.transform = 'scale(.85)';
  }, true);
  addEventListener('pointerup', () => {
    dot.style.opacity = 0; dot.style.transform = 'none';
  }, true);
});
"""


def run(*args: str | Path) -> None:
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *map(str, args)], check=True)


def tap(page: Page, locator, hold_ms: int = 120) -> None:
    box = locator.bounding_box()
    page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(hold_ms)
    page.mouse.up()


def record(browser: Browser, name: str, extra: str, act) -> list[float]:
    """Record one scene; ``act`` returns cut points (seconds since the card was ready)."""
    with tempfile.TemporaryDirectory() as tmp:
        context = browser.new_context(
            viewport={"width": WIDTH, "height": HEIGHT},
            record_video_dir=tmp,
            record_video_size={"width": WIDTH, "height": HEIGHT},
        )
        context.grant_permissions(["microphone"], origin=ORIGIN)
        context.add_init_script(TOUCH.replace("__ZOOM__", str(ZOOM)))
        page = context.new_page()
        open_card(page, "mobile", "dark", extra)
        ready = page.evaluate("performance.now()") / 1000
        marks = [m - ready for m in act(page)]
        page.wait_for_timeout(300)
        video = page.video.path()
        context.close()
        shutil.copy(video, BUILD / f"{name}.webm")
    return [max(0.0, m) for m in marks]


def now(page: Page) -> float:
    return page.evaluate("performance.now()") / 1000


def talk_scene(page: Page) -> list[float]:
    card = page.locator("roomcall-card")
    start = now(page)
    page.wait_for_timeout(900)
    tap(page, card.locator(".rooms .chip", has_text="Basement"))
    page.wait_for_timeout(900)
    split = now(page)
    talk = card.locator("button.talk").bounding_box()
    page.mouse.move(talk["x"] + talk["width"] / 2, talk["y"] + talk["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(2600)
    page.mouse.up()
    accepted(page)
    page.wait_for_timeout(1500)
    return [start, split, now(page)]


def accepted(page: Page) -> None:
    page.wait_for_function(
        "document.querySelector('roomcall-card').shadowRoot"
        ".querySelector('.status').textContent.startsWith('Playback requests accepted')"
    )


def reply_scene(page: Page) -> list[float]:
    card = page.locator("roomcall-card")
    page.wait_for_timeout(700)  # reply mode re-renders the room icons
    start = now(page)
    page.wait_for_timeout(1000)
    talk = card.locator("button.talk").bounding_box()
    page.mouse.move(talk["x"] + talk["width"] / 2, talk["y"] + talk["height"] / 2)
    page.mouse.down()
    page.wait_for_timeout(1800)
    page.mouse.up()
    accepted(page)
    page.wait_for_timeout(1500)
    return [start, now(page)]


def doorbell_scene(page: Page) -> list[float]:
    card = page.locator("roomcall-card")
    start = now(page)
    page.wait_for_timeout(1000)
    page.evaluate("""() => {
      const h = ROOMCALL_HARNESS, card = document.querySelector("roomcall-card");
      h.hass.states["binary_sensor.front_door_visitor"] = {
        state: "on", last_changed: new Date().toISOString(), attributes: {} };
      card.hass = {...h.hass};
    }""")
    page.wait_for_timeout(2600)
    tap(page, card.locator(".doorbell").get_by_role("button", name="Answer", exact=True))
    page.wait_for_timeout(1000)
    return [start, now(page)]


def text_filter(caption: str) -> str:
    if not caption:
        return ""
    safe = caption.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\u2019")
    return (
        f",drawtext=fontfile={FONT}:text='{safe}':fontcolor=white:fontsize=64"
        ":box=1:boxcolor=black@0.85:boxborderw=24:x=(w-text_w)/2:y=24"
    )


def normalise(
    src: Path,
    dst: Path,
    caption: str,
    start: float = 0,
    end: float | None = None,
    audio: Path | None = None,
) -> None:
    """Fill 1080x1920 at 30 fps with a caption; audio from ``audio``, the clip, or silence."""
    trim = ["-ss", f"{start:.2f}"] + (["-to", f"{end:.2f}"] if end else [])
    video = (
        f"scale={WIDTH}:{HEIGHT}:force_original_aspect_ratio=increase,"
        f"crop={WIDTH}:{HEIGHT},fps={FPS},format=yuv420p{text_filter(caption)}"
    )
    if audio:
        sound, extra = "[1:a]aresample=48000,apad", ["-i", audio]
    elif has_audio(src):
        sound, extra = "[0:a]aresample=48000,apad", []
    else:
        sound, extra = "[1:a]anull", ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    run(
        *trim, "-i", src, *extra, "-filter_complex", f"[0:v]{video}[v];{sound}[a]",
        "-map", "[v]", "-map", "[a]", "-shortest", "-c:v", "libx264", "-crf", "18",
        "-preset", "veryfast", "-c:a", "aac", "-ac", "2", "-ar", "48000", dst,
    )  # fmt: skip


def has_audio(path: Path) -> bool:
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a", "-show_entries", "stream=index",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )  # fmt: skip
    return bool(probe.stdout.strip())


def placeholder(dst: Path, caption: str, label: str) -> None:
    run(
        "-f", "lavfi", "-i", f"color=c=0x1c1f26:s={WIDTH}x{HEIGHT}:d=4:r={FPS}",
        "-vf", f"format=yuv420p{text_filter(caption)},drawtext=fontfile={FONT}"
        f":text='[owner footage\\: {label}]':fontcolor=0xffd60a:fontsize=48:x=(w-text_w)/2:y=h/2",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", dst.with_suffix(".silent.mp4"),
    )  # fmt: skip
    normalise(dst.with_suffix(".silent.mp4"), dst, "")


def end_card(dst: Path) -> None:
    lines = [
        ("RoomCall", 110, 1080),
        ("Push-to-talk for Home Assistant", 54, 1230),
        ("Free and open source", 54, 1310),
        ("github.com/daredoole/RoomCall", 48, 1440),
    ]
    text = "".join(
        f",drawtext=fontfile={FONT}:text='{t}':fontcolor=white:fontsize={s}:x=(w-text_w)/2:y={y}"
        for t, s, y in lines
    )
    run(
        "-f", "lavfi", "-i", f"color=c=0x0b0d12:s={WIDTH}x{HEIGHT}:d=4:r={FPS}",
        "-i", ASSETS / "logo.png", "-filter_complex",
        f"[1:v]scale=300:-1[l];[0:v][l]overlay=(W-w)/2:620,format=yuv420p{text}",
        "-c:v", "libx264", "-crf", "18", "-preset", "veryfast", dst.with_suffix(".silent.mp4"),
    )  # fmt: skip
    normalise(dst.with_suffix(".silent.mp4"), dst, "")


def assemble(clips: list[Path], out: Path) -> None:
    inputs = [arg for clip in clips for arg in ("-i", clip)]
    streams = "".join(f"[{i}:v][{i}:a]" for i in range(len(clips)))
    run(
        *inputs, "-filter_complex",
        f"{streams}concat=n={len(clips)}:v=1:a=1[v][a0];[a0]loudnorm=I=-16:TP=-1.5:LRA=11[a]",
        "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-crf", "28", "-maxrate", "1600k",
        "-bufsize", "3200k", "-preset", "slow", "-movflags", "+faststart",
        "-c:a", "aac", "-b:a", "96k", "-ar", "48000", out,
    )  # fmt: skip


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--footage", type=Path, help="Directory with the owner's real clips")
    args = parser.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(
            args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream"]
        )
        talk = record(browser, "talk", "&card=simple", talk_scene)
        reply = record(browser, "reply", "&card=simple&roomcall=kitchen", reply_scene)
        door = record(browser, "doorbell", "&card=simple&doorbell=on", doorbell_scene)
        browser.close()
    final = args.footage is not None
    # Real audio recorded for a card scene (for example the voice while holding to talk).
    sound = {p.stem: p for p in args.footage.glob("*.wav")} if final else {}
    captions = {name: caption for name, caption, _ in SCENES}
    cuts = {
        "01-pick": ("talk", talk[0], talk[1]),
        "02-talk": ("talk", talk[1], talk[2]),
        "05-reply": ("reply", *reply),
        "07-doorbell": ("doorbell", *door),
    }
    for name, (scene, start, end) in cuts.items():
        normalise(
            BUILD / f"{scene}.webm", BUILD / f"{name}.mp4", captions[name], start, end,
            sound.get(name),
        )  # fmt: skip
    end_card(BUILD / "08-end.mp4")

    clips = []
    for name, caption, source in SCENES:
        clip = BUILD / f"{name}.mp4"
        if source == "footage":
            real = args.footage / f"{name}.mp4" if final else None
            if real and real.exists():
                normalise(real, clip, caption)
            elif final:
                parser.error(f"missing owner footage: {real}")
            else:
                placeholder(clip, caption, name.split("-", 1)[1])
        clips.append(clip)
    out = ASSETS / "demo.mp4" if final else BUILD / "demo-preview.mp4"
    assemble(clips, out)
    if final:
        poster = ASSETS / "demo-poster.jpg"
        run("-ss", "4", "-i", out, "-frames:v", "1", "-vf", "scale=540:-1", poster)
    size = out.stat().st_size / 1_000_000
    print(f"{out.relative_to(ROOT)}: {size:.1f} MB" + ("" if final else " (preview, placeholders)"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
