"""Install integration dependencies pinned by the HA version under test."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import homeassistant

requirements = []
for domain in ("conversation", "tts"):
    manifest = Path(homeassistant.__file__).parent / f"components/{domain}/manifest.json"
    requirements.extend(json.loads(manifest.read_text())["requirements"])
subprocess.run(["uv", "pip", "install", "--python", sys.executable, *requirements], check=True)
