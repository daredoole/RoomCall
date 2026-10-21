# Contributing to RoomCall

Thanks for helping! RoomCall stays small on purpose: one job, done well.

## Development setup

```bash
git clone https://github.com/daredoole/RoomCall
cd RoomCall
uv venv && uv pip install -e ".[dev,all]"
pytest            # unit + API tests, no real speakers needed
ruff check . && ruff format --check .
mypy
```

The test suite uses fake speaker backends, so it runs anywhere. To try real hardware:

```bash
roomcall discover
roomcall serve -v
```

## Before you commit

```bash
pipx install pre-commit && pre-commit install   # ruff, JSON/YAML checks on every commit
```

## Home Assistant integration

The integration lives in `custom_components/roomcall/` and is tested with
[pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component):

```bash
uv venv .venv-ha --python 3.13
uv pip install --python .venv-ha pytest-homeassistant-custom-component ha-ffmpeg "HAP-python==5.0.0"
.venv-ha/bin/pytest tests_ha
```

Validate it the way CI does (on SELinux hosts add `--security-opt label=disable`):

```bash
docker run --rm -v "$PWD:/github/workspace:ro" ghcr.io/home-assistant/hassfest
```

If you change the card, regenerate the screenshots with `python scripts/ha_screenshots.py`
(needs `playwright install chromium`). The card renders in a harness with a fake `hass` object,
so no Home Assistant instance is needed.

## Releasing

1. Bump `version` in `custom_components/roomcall/manifest.json`, `pyproject.toml` and
   `src/roomcall/__init__.py`, the card version in `frontend/roomcall-card.js` and its `?v=`
   cache-buster in `__init__.py`; move the `CHANGELOG.md` entries under the new version.
2. Tag and push: `git tag v0.1.1 && git push --tags`. The release workflow checks the versions
   match, builds the Python package and `roomcall.zip` for HACS, and publishes the GitHub release.

## Adding a speaker backend

1. Add a class in `src/roomcall/sinks/` with `async def play(self, speaker, url, volume)`.
2. Import its optional library **inside** `play` and raise `SinkUnavailable` if missing.
3. Register the mDNS service type in `discovery.SERVICE_TYPES` and the kind in `models.SpeakerKind`.
4. Add parsing tests in `tests/test_discovery.py`.

## Pull requests

- Keep changes focused; include tests for behaviour changes.
- CI must pass (lint, types, tests on 3.12 and 3.13, docs build).
- Speaker compatibility reports (model + firmware + measured delay) are very welcome as issues.
