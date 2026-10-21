# Communication roadmap implementation

This plan implements the five requested stages on `feat/communication-platform`.
The Home Assistant integration is the implementation target; the standalone server
continues to provide its existing push-to-talk API.

## Shared design

- Keep `roomcall.broadcast` backward compatible and use the same call pipeline for
  voice recordings, automations, and agent requests.
- Model observations honestly: request accepted, audio fetched, playback reported,
  and human acknowledgment are separate evidence. Never infer audible playback
  from a successful service call.
- Keep bounded message jobs and serialize use of each physical endpoint. Give
  every asynchronous operation an ID, expiry, and observable terminal state.
- Require authenticated control; browser station ownership and valid HA room assignment
  are enforced on the server. Microphones open only after local interaction.
- Store voicemail only when enabled. Translation uses an explicitly selected
  Home Assistant AI task provider; language preferences use the selected TTS
  provider. Neither feature silently selects a cloud service.
- Integrate existing HA satellite and SIP actions. Advertise only capabilities
  actually available from the installed integrations and connected stations.

## First: correctness and agent compatibility

- [x] Current HA `llm.py` tools and compatibility with older supported versions.
- [x] Accurate Assist results, structured errors, caller context, exposure checks.
- [x] Delivery evidence and bounded, local-only metrics.
- [x] Tests enumerate actual tools and exercise failure/recovery.

## Next: setup and discovery

- [x] Capability discovery, exact delivery previews, and diagnostics operations.
- [x] Conservative physical-speaker deduplication using device identity or explicit
  alias configuration, never just matching manufacturer/model/room.
- [x] Guided sound tests with explicit human confirmation and setup timing.
- [x] Dashboard Talk/Type/Ask flows and accessible controls.

## Then: communication jobs

- [x] Submit/status/cancel/acknowledge operations with idempotency and expiration.
- [x] Per-endpoint serialization through playback/restoration, bounded queues.
- [x] Satellite questions with response/timeout status and no fabricated answers.
- [x] Tests cover concurrent jobs, overlapping routes, cancellation, and reload.

## Afterward: stations and adapters

- [x] Authenticated browser station registration and incoming call UI.
- [x] WebRTC signaling with ownership, consent, disconnect cleanup, and timeouts.
- [x] Presence freshness/confidence plus explicit person preferences.
- [x] Explicit SIP/doorbell adapters to configured HA scripts, with discovery and
  validation. Hardware/protocol support remains that of the underlying adapter.

## Later: optional processing and retention

- [x] Opt-in voicemail with bounded persistent storage, expiry, and deletion.
- [x] Explicit translation provider and per-recipient language preferences.
- [x] Optional speech normalization/noise filtering/limiting verified with ffmpeg.

## Completion checks

- [x] All user-visible additions translated into en/es/de/fr/nl.
- [x] Version fields and changelog updated together.
- [x] Ruff, formatting, mypy, standalone tests, HA tests (85% coverage), docs,
  frontend checks and package builds.
- [x] HACS validation and hassfest in CI.
- [x] Browser QA at desktop/mobile sizes; screenshots updated.
- [x] [Pull request #42](https://github.com/daredoole/RoomCall/pull/42) created and
  CI results inspected. No direct push to main.

## Measurement contract

Diagnostics expose setup confirmation rate/time; request and fetch latency by
route; reported playback-start latency where observable; expirations, failures,
retries, duplicate requests suppressed, and acknowledgments. First *audible*
audio and whether a human heard an unacknowledged message require a person or
instrumented endpoint: unsupported measurements remain unknown.

Agent acceptance scenarios cover discovery → preview → send → status, blocked
quiet hours → diagnosis → ordinary recovery, and asking a question → response.

## Local verification

- HA 2026.10.2 / Python 3.14: 126 passed; 89.65% integration coverage.
- HA 2026.2.3 / Python 3.13: 125 passed, one current-HA-only skip; 89.78% coverage.
- Standalone: 17 passed. Ruff, formatting, mypy, strict docs and wheel/sdist builds passed.
- Desktop/mobile messaging and real browser WebRTC passed with synthetic microphones;
  physical speakers, SIP devices and doorbells were not available for acoustic testing.
