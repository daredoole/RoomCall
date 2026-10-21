# Notes for AI coding agents

- **Using RoomCall** in a Home Assistant install (not changing its code): read
  [`docs/ai.md`](docs/ai.md) — actions, the Assist intent, announcement subentries, entities.
- The Home Assistant integration is `custom_components/roomcall/`. The `src/roomcall/` package is
  the separate standalone server.

## Checks before a pull request

```bash
uv venv && uv pip install -e ".[dev]"
uv venv .venv-ha --python 3.13
uv pip install --python .venv-ha pytest-homeassistant-custom-component ha-ffmpeg "HAP-python==5.0.0" pytest-cov
.venv-ha/bin/python scripts/install_ha_test_deps.py
ruff check . && ruff format --check . && mypy
pytest --cov=src/roomcall --cov-fail-under=100
.venv-ha/bin/pytest tests_ha --cov=custom_components/roomcall --cov-fail-under=100
docker run --rm -v "$PWD:/github/workspace:ro" ghcr.io/home-assistant/hassfest
```

The communication suite is exercised in CI against Home Assistant 2026.2.3 and
2026.10.2. Browser communication QA: `python scripts/qa_communication.py`
(requires Playwright Chromium). Do not replace unknown audibility with inferred
success when changing delivery evidence or metrics.

Both Python packages require **100% statement coverage**, with no source omissions
or coverage exclusions added to meet the threshold. CI enforces this on every
supported Python/HA test job. Browser QA also checks the card at 240–600px widths,
with expanded controls and auto/fixed-height dashboard cells; Python coverage does
not measure JavaScript execution or prove real speaker audibility.

## Conventions

- Every user-visible string is in `strings.json` and `translations/*.json` (en, es, de, fr, nl).
  Keep translations complete; hassfest rejects HTML-looking text such as `<ip>`.
- Bump the version in `manifest.json`, `const.py` (`VERSION`), `pyproject.toml`,
  `src/roomcall/__init__.py` and the card's `CARD_VERSION` together; add a `CHANGELOG.md` entry.
- New behaviour needs a test in `tests_ha/`. Use real ffmpeg where audio matters.
- `main` is protected: open a pull request; all CI checks must pass.
