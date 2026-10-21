"""Install the conversation dependencies pinned by the HA version under test."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import homeassistant

manifest = Path(homeassistant.__file__).parent / "components/conversation/manifest.json"
requirements = json.loads(manifest.read_text())["requirements"]
subprocess.run(["uv", "pip", "install", "--python", sys.executable, *requirements], check=True)
