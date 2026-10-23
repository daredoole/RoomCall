"""Record/check screenshot integrity and the inputs used to capture the interface.

Refresh the captures before --record; this records provenance, it does not capture UI.
See docs/screenshots.md. No Home Assistant connection or credentials are used.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/assets/screenshots.json"
GROUPS = {
    "home_assistant": {
        "images": ["docs/assets/ha-*.png", "docs/assets/demo.gif"],
        "inputs": [
            "custom_components/roomcall/frontend/*.js",
            "custom_components/roomcall/strings.json",
            "scripts/ha_card_harness.html",
            "scripts/ha_screenshots.py",
            "scripts/qa_communication.py",
        ],
    },
    "standalone": {
        "images": ["docs/assets/screenshot-*.png"],
        "inputs": ["src/roomcall/web/*", "src/roomcall/demo.py", "scripts/screenshots.py"],
    },
    "ios": {
        "images": ["docs/assets/ios-*.png"],
        "inputs": [
            "ios/RoomCall/Sources/*.swift",
            "ios/RoomCall/Shared/*.swift",
            "ios/RoomCall/Controls/*.swift",
            "ios/RoomCall/Resources/**/*.strings",
            "ios/RoomCall/Resources/Assets.xcassets/**/*",
            "ios/RoomCall/project.yml",
            "ios/RoomCall/UITests/*.swift",
            "scripts/ios_mockups.py",
        ],
    },
}


def hashes(patterns: list[str]) -> dict[str, str]:
    return {
        str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted({p for pattern in patterns for p in ROOT.glob(pattern) if p.is_file()})
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--ios-run", help="Successful iOS workflow run ID for refreshed captures")
    parser.add_argument("--ios-commit", help="Source commit of that iOS workflow run")
    args = parser.parse_args()
    current = {
        name: {kind: hashes(patterns) for kind, patterns in group.items()}
        for name, group in GROUPS.items()
    }
    if args.record:
        if not args.ios_run or not args.ios_commit:
            parser.error("--record requires --ios-run and --ios-commit; refresh all captures first")
        native_paths = list(current["ios"]["inputs"])
        subprocess.run(
            ["git", "diff", "--exit-code", args.ios_commit, "--", *native_paths],
            cwd=ROOT,
            check=True,
            stdout=subprocess.DEVNULL,
        )
        record = {
            "schema": 1,
            "capture_base_commit": subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
            ).strip(),
            "ios_source_commit": args.ios_commit,
            "ios_run": f"https://github.com/daredoole/RoomCall/actions/runs/{args.ios_run}",
            "sample_data": True,
            "groups": current,
        }
        MANIFEST.write_text(json.dumps(record, indent=2) + "\n")
    record = json.loads(MANIFEST.read_text())
    failures = []
    for name, group in current.items():
        for kind, actual in group.items():
            expected = record["groups"][name][kind]
            for path in sorted(set(actual) | set(expected)):
                if actual.get(path) != expected.get(path):
                    failures.append(f"{name} {kind}: {path}")
    if failures:
        print("Screenshot record is stale; refresh/review captures per docs/screenshots.md:")
        print("\n".join(failures))
        return 1
    count = sum(len(group["images"]) for group in current.values())
    print(f"Verified {count} screenshot/demo assets and their recorded interface inputs.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
