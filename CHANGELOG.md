# Changelog

All notable changes are listed here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/).

## 0.1.1 — unreleased

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
