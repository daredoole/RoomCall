# Contributing to RoomCall

Thanks for helping! RoomCall stays small on purpose: one job, done well.

## Development setup

```bash
git clone https://github.com/daredoole/RoomCall
cd RoomCall
uv venv && uv pip install -e ".[dev,all]"
pytest --cov=src/roomcall --cov-fail-under=100
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
uv pip install --python .venv-ha "pytest-homeassistant-custom-component==0.13.316" ha-ffmpeg "HAP-python==5.0.0" pytest-cov
.venv-ha/bin/python scripts/install_ha_test_deps.py
.venv-ha/bin/pytest tests_ha --cov=custom_components/roomcall --cov-fail-under=100
```

This environment tests HA 2026.2.3. CI also uses Python 3.14 and
`pytest-homeassistant-custom-component==0.13.373` for HA 2026.10.2. Both packages
require 100% statement coverage, without source omissions or added exclusions.
Use real ffmpeg for audio regressions. The percentage does not cover Swift or JS.

Validate it the way CI does (on SELinux hosts add `--security-opt label=disable`):

```bash
docker run --rm -v "$PWD:/github/workspace:ro" ghcr.io/home-assistant/hassfest
```

If you change the UI, follow [the screenshot refresh procedure](docs/screenshots.md).
Card captures use a fake `hass` object; no HA instance is needed. Run
`python scripts/qa_communication.py` for messaging, narrow/fixed-height layouts
and WebRTC checks, then `python scripts/doc_assets.py` and `mkdocs build --strict`.

## Releasing

1. Bump all five versions together: `custom_components/roomcall/manifest.json`,
   `custom_components/roomcall/const.py` (`VERSION`), `pyproject.toml`,
   `src/roomcall/__init__.py` and `custom_components/roomcall/frontend/roomcall-card.js`
   (`CARD_VERSION`). The dashboard resource derives its cache-buster from `VERSION`.
   Add a `CHANGELOG.md` entry. For native releases, update `MARKETING_VERSION` and
   increment `CURRENT_PROJECT_VERSION` in `ios/RoomCall/project.yml` too.
2. Update pinned standalone wheel examples, regenerate affected screenshots and
   their capture record, and validate docs. Open a PR; every required check must pass.
3. After merging, tag the tested commit and push only that tag, for example
   `git tag -a vX.Y.Z MERGE_COMMIT -m "RoomCall X.Y.Z"` then
   `git push origin refs/tags/vX.Y.Z`. The release workflow checks all five versions,
   builds the wheel/sdist and `roomcall.zip`, and attests those artifacts. PyPI
   publishing is opt-in and is not currently enabled.
4. The unsigned IPA is produced separately by iOS CI. If attaching it, verify its
   version/build, matching native source and checksum; document the source run
   and that it is outside the integration/Python provenance bundle.

## Adding a speaker backend

1. Add a class in `src/roomcall/sinks/` with `async def play(self, speaker, url, volume)`.
2. Import its optional library **inside** `play` and raise `SinkUnavailable` if missing.
3. Register the mDNS service type in `discovery.SERVICE_TYPES` and the kind in `models.SpeakerKind`.
4. Add parsing tests in `tests/test_discovery.py`.

## Pull requests

- Keep changes focused; include tests for behaviour changes.
- CI must pass (lint, types, tests on 3.12 and 3.13, docs build).
- Speaker compatibility reports (model + firmware + measured delay) are very welcome as issues.
