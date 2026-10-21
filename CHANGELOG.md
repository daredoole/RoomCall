# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

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
