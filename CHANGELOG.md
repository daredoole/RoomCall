# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## 0.16.0 — 2026-10-23

- Named household profiles provide reusable targets, message, volume, language, chime and voice defaults through a dashboard picker and the existing preview/send API.

## 0.13.0 — 2026-10-23

- Complete the sideloadable native iOS app with task controls, history, questions,
  widgets, Live Activity and QR-based Home Assistant connection.
- Correct shifted translations across all five languages, including Send, doorbell,
  task confirmation and Do Not Disturb labels. Add regression checks for native
  label meanings and format arguments; retain end-to-end simulator assertions.

## 0.12.0 — 2026-10-22

- Hands-free talk-back ([#11](https://github.com/daredoole/RoomCall/issues/11)): with an Assist
  satellite in a room (Atom Echo, ESP32-S3, Home Assistant Voice), a room-to-room call listens
  for a spoken reply and plays its transcription in the caller's room. Opt in under
  Configure → Communication; silent replies send nothing and replies never ask for replies.
- Answer the door ([#21](https://github.com/daredoole/RoomCall/issues/21)): choose a doorbell,
  its camera and rooms under Configure → Communication. A press is announced in those rooms,
  phones and watches get the snapshot with an **Answer** button, and RoomCall cards show a
  banner; Answer opens the camera's live view, with two-way talk where the camera supports it.

## 0.11.0 — 2026-10-22

- Announcement tasks: give a scheduled or triggered announcement a task to confirm (for example
  "Took out the dry clothes"). It stays open and repeats every few minutes until someone confirms
  it from the phone notification, the card's new **To do** list, a **Mark done** button entity or
  the `tasks` operation. Dismissing a notification never counts as done; **Remind me later**
  snoozes for 30 minutes. Open tasks survive restarts.
- Each task gets a **Task** sensor (`idle`/`open`/`done`, who and when) and fires
  `roomcall_task_completed`. Task notifications go to the announcement's people when they have
  the app, stay pinned on Android, and replace the generic call notification.

## 0.10.2 — 2026-10-22

- Generate native Home Assistant Perform Action templates with a live room picker,
  optional multiple rooms, and text supplied by another shortcut.
- Discover friendly destination choices from permitted RoomCall speakers on every run;
  keep Everywhere explicit and reject empty live-picker targets.
- Configure the Companion server and action on import without embedding private IDs,
  addresses or credentials. Native exports support local or opt-in account signing.
- Remove the incorrect blank-message conditional from portable URL exports.

## 0.10.1 — 2026-10-22

- Include the configured chime in typed, Siri, notify and scheduled announcements
  without requiring Chime TTS; synthesize once per message and report preparation
  failures explicitly. Record local TTS preparation timing and hosted fetch evidence.
- Add room-specific Siri setup in the card, recommending Home Assistant Perform Action
  to avoid URL confirmation; retain downloadable portable URL shortcuts with clear
  signing requirements and optional consent-based signing using the user's HubSign key.
- Advertise shortcut setup/export metadata to agents; translate the controls in all
  five languages and cover narrow layouts, signing failures and real chime audio.

## 0.10.0 — 2026-10-21

- Adapt the dashboard to its container width, retain Talk/Type/Ask in compact layouts,
  enlarge touch targets, and move setup/stations into a keyboard-accessible modal panel.
- Separate setup review from editing and remove duplicate active-plan sound tests.
- Add an iOS SwiftUI prototype with HA OAuth/PKCE and Keychain storage, native Push to
  Talk channel control, live 16 kHz audio delivery, Siri announcements and a Control
  Center launcher. Incoming background audio requires a future provisioned push service.
- Advertise the native OAuth callback from HA without exposing credentials; generate
  native translations from the five integration catalogs and build/test in macOS CI.

## 0.9.0 — 2026-10-21

- Bootstrap RoomCall's tools by opening HA's configuration flow before an entry exists;
  add a versioned operation catalog, native settings schemas and executable error recovery.
- Persist administrator-owned setup plans with review, revision checks, native apply,
  crash recovery, human-confirmed audio verification, resume and scoped undo.
- Add dedicated announcement schema/CRUD/enable tools with revision checks, persistent
  bounded idempotency and native HA configuration flows.
- Diagnose setup failures and propose alternate routes only for verified physical
  speaker identities; repair proposals never apply themselves or bypass policy.
- Share guided setup between the card and agents; cover 240px/mobile/desktop,
  interrupted setup, authorization and both HA schema engines. Add protocol-agent
  acceptance reports alongside the existing 100% Python statement coverage gates.

## 0.8.1 — 2026-10-21

- Size dashboard cards to their contents, wrap narrow columns, and contain forced-height
  layouts so adjacent cards remain usable. Cover compact/default/kiosk layouts in browser QA.
- Require 100% Python statement coverage for the integration and standalone server in CI,
  including supported HA versions, failure recovery, adapters, and resource cleanup.

## 0.8.0 — 2026-10-21

- Explicit Home Assistant LLM tools, an older-HA RoomCall API, caller permissions
  and exposure checks, structured errors, exact recipient previews and diagnostics.
- Accurate Assist/card results: playback request acceptance, hosted audio fetches,
  matching player reports and explicit human acknowledgment are separate evidence.
- Bounded message jobs with IDs, idempotency, expiry, queued cancellation, endpoint
  serialization, acknowledgment deadlines, configured phone fallback and satellite Q&A.
- Talk/Type/Ask dashboard controls, keyboard push-to-talk, persistent human-confirmed
  sound tests and conservative physical-speaker deduplication/explicit aliases.
- Consent-based WebRTC audio between browser room stations, with authenticated
  signaling, ownership checks, disconnect cleanup and session deadlines.
- Presence freshness/confidence and person preferences; configured SIP/doorbell
  script adapters. Adapter audio transport remains with the underlying integration.
- Opt-in bounded local voicemail with expiry/deletion/replay, explicit translation
  providers, TTS language preferences and optional ffmpeg recorded-speech processing.
- Bounded local delivery/setup metrics; new copy translated into five languages.
- HA compatibility tests on 2026.2.3/2026.10.2 and desktop/mobile browser/WebRTC QA.

## 0.7.2 — 2026-10-21

- Fix "Configuration error" on the RoomCall card until the page is refreshed: the card is now registered as a dashboard resource (kept on the current version, duplicates removed), so dashboards load it before drawing. YAML-mode dashboards keep the previous loading method.

## 0.7.1 — 2026-10-21

- Fix live calls to AV receivers (Denon/HEOS) being cut off: the receiver was switched back to its previous input about 2 s in, because a live call has no known length and HEOS never reports "playing". RoomCall now restores after the live call ends plus a 5 s buffer, and for messages of unknown length waits 12 s when the player never reports playing.

## 0.7.0 — 2026-10-21

- **Scheduled announcements**: a message at a time of day on chosen weekdays.
- **Triggered announcements**: a message when any entity changes to a state (light switch, door,
  motion, washer…), with an optional "for at least" hold and a cooldown.
- Both are config subentries (Add announcement on the integration), with templated messages,
  targets by room, specific speaker or person, urgent priority, and per-announcement
  **Play now** button and **Enabled** switch.
- `roomcall.broadcast` accepts `speakers:` — including media players outside the intercom.
- **Assist intent `RoomCallAnnounce`**, offered to every LLM conversation agent.
- Docs: Announcements, Managing RoomCall with AI, `llms.txt`, `AGENTS.md`.
- The options flow reloads through an update listener (needed for announcement changes).

## 0.6.2 — 2026-10-20

- Releases include `roomcall.intoto.jsonl`, the signed build provenance for every release file (verify with `gh attestation verify roomcall.zip --repo daredoole/RoomCall`). Workflows use least-privilege, job-level permissions.

## 0.6.1 — 2026-10-20

- Repairs: don't report speakers as unavailable while Home Assistant is still starting. A speaker is reported only after 5 minutes unavailable, and the alert clears as soon as it is back.

## 0.6.0 — 2026-10-20

- **Visual card editor**: set up the card without YAML; it also previews in the card picker.
- **Notify entity per room** (`notify.roomcall_<room>`): `notify.send_message` speaks there.
- **Blueprints** with one-click import: doorbell, appliance finished, bedtime Do Not Disturb,
  arrivals.
- **Repairs** for unavailable speakers, a missing local URL and an HTTPS internal URL;
  **diagnostics** download with what people said redacted.
- **Translations**: Spanish, German, French and Dutch.
- Project: OpenSSF Scorecard, coverage gate (≥85%) in CI, SHA-pinned actions, signed build
  provenance for release files, pre-commit config, animated demo in the README.

## 0.5.0 — 2026-10-20

- **Live calls**: speakers start playing while you are still talking. The card streams 16 kHz
  audio over Home Assistant's WebSocket (`roomcall/live`), RoomCall encodes it to MP3 as it
  arrives — chime first — and speakers play one growing URL (`/api/roomcall/live/<id>.mp3`).
  History, transcripts and notifications follow when you let go. On by default; set
  `live: false` on the card for the previous record-then-send behaviour.
- **Apple Home goes live too**: a call starts as soon as you start speaking in the Home app.
- The card falls back to record-then-send against older RoomCall versions.

## 0.4.5 — 2026-10-20

- Apple Home: cut talk-back delay from ~10 s to about 2 s. A phrase now ends 0.5 s after the iPhone stops sending (it stops instead of sending silence when the talk button is released), and ffmpeg runs without probing, buffering or a jitter queue.

## 0.4.4 — 2026-10-20

- Apple Home: fix talk-back never playing — the talk receiver bound to the iPhone's address ("bind failed: Address not available") instead of Home Assistant's own. Back off when the receiver fails to start.

## 0.4.3 — 2026-10-20

- Apple Home: use the talk-back payload type the iPhone negotiates, and log stream settings, ffmpeg errors and decoded audio for troubleshooting.

## 0.4.2 — 2026-10-20

- Apple Home: also load saved pairing state off the event loop.

## 0.4.1 — 2026-10-20

- Apple Home: create HomeKit accessories off the event loop (fixes a "Detected blocking call to open" warning).

## 0.4.0 — 2026-10-20

- **Apple Home intercom** (opt-in, Configure → Apple Home): every room appears in the Home app as
  a camera with a talk button. Talking in Home, on Apple Watch or from Control Center plays in
  that room — with the chime, history, transcripts and notifications like any call. RoomCall acts
  as a HomeKit camera bridge (HAP-python), decodes the iPhone's SRTP/Opus talk-back with ffmpeg
  and splits it into phrases with voice-activity detection.
- Calls from the Home app are labelled "Apple Home"; Reply buttons only target real rooms.

## 0.3.0 — 2026-10-20

- **AV receivers and TV boxes**: speakers that can't announce (Denon/Marantz through HEOS,
  Android TV / Google TV boxes such as onn through Cast) are put back after a call: input
  source, volume, power and the app that was open are restored, using the receiver's
  `denonavr` entity or the box's Android TV Remote entity in the same room. Overlapping calls
  share one snapshot. New option: Delivery → Put speakers back after a call (on).
- Brand icon shipped with the integration (`brand/`), shown in Home Assistant 2026.3+.
- Options sections keep their saved values when a section is left out.

## 0.2.0 — 2026-10-20

- **Reply from notifications**: each call can notify chosen phones (skipping the caller's own)
  with a Reply button that opens the card aimed back at the caller's room; cards take `room:`.
- **Call a person**: `people:` in `roomcall.broadcast` and person buttons on the card play wherever
  that person is, using room-presence sensors such as Bermuda.
- **Do Not Disturb** switch, **quiet hours** (lower volume, skipped rooms) and **urgent** calls
  that override both.
- **Call history** on the card and the sensor, with replay of the last 20 calls for 24 hours.
- **Speech-to-text transcripts** in history and notifications.
- **Chimes**: walkie-talkie, ding, double beep, rising, doorbell, none, or your own file/URL; typed
  messages use the same chime through Chime TTS.
- **Delivery**: speakers start 250 ms apart and failed speakers are retried once.
- **Card layouts**: `compact` and `kiosk`, Urgent and Do Not Disturb toggles, clearer HTTPS hint.
- Options are grouped into sections. Fix: saving options failed with "Config entry update
  listeners should not be used with OptionsFlowWithReload".
- New guide: Siri, Apple Watch and CarPlay with Shortcuts.

## 0.1.2 — 2026-10-20

- Fix push-to-talk from iPhone/Safari behind a reverse proxy: the upload was read only up
  to the first network chunk, so ffmpeg failed with "Error opening input: End of file".

## 0.1.1 — 2026-10-20

- Walkie-talkie chirp before push-to-talk clips and (with Chime TTS) typed messages;
  new **Play a chime first** option.
- Card refuses silent recordings and shows the clip length and which speakers played it.
- HACS one-click install, release `roomcall.zip`, hassfest + HACS validation workflow.
- Home Assistant-first README and docs with card screenshots.

## 0.1.0

- mDNS discovery of Sonos, AirPlay and Cast speakers with per-device protocol preference.
- Rooms, groups and "everywhere" targets with forgiving name matching.
- Browser hold-to-talk app with live level meter and per-speaker delivery report.
- Sonos (snapshot/restore), AirPlay (pyatv) and Cast (pychromecast) backends.
- HTTP API, `roomcall` CLI (`discover`, `serve`, `send`), optional token auth.
- Demo mode with fake speakers; screenshot automation for the docs.
- Home Assistant integration: config flow, areas as rooms, push-to-talk card,
  `roomcall.broadcast` action, last-call sensor, HACS metadata and CI tests.
