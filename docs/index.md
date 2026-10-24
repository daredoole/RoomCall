---
title: RoomCall — push-to-talk intercom for Home Assistant
description: Hold a button, talk, and your voice plays in the room you picked, on the Sonos, Google Cast and AirPlay speakers Home Assistant already controls.
---

# RoomCall

**A push-to-talk intercom for Home Assistant, on the speakers you already own.** Hold a button,
talk, and your voice plays in the room you picked — on Sonos, Google Cast or AirPlay.

[![Open your Home Assistant instance and open RoomCall in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=daredoole&repository=RoomCall&category=integration)

<div class="grid" markdown>
![Pick a room, hold to talk, and it plays live](assets/demo.gif){ width="260" }
![The card: rooms, a big talk button and the latest call](assets/ha-card-simple.png){ width="240" }
</div>

The real card in a test harness with sample rooms; it shows the interface, not a speaker
playing ([how screenshots are made](screenshots.md)).

## Get started in three steps

1. **Install** RoomCall from HACS and restart Home Assistant.
2. **Add the integration** and pick one media player per speaker.
3. **Add the RoomCall card** to a dashboard, open it in the Home Assistant app or over HTTPS,
   pick a room and hold to talk.

Then check each room once with **More → Settings → Setup and sound tests**.
[Full setup guide →](home-assistant.md){ .md-button .md-button--primary }

## What you can do with it

- **"Dinner's ready!"** — talk to one room, everywhere, or a person wherever they are; or
  type it and hear it in your Home Assistant voice.
- **Answer the door from any room** — the doorbell rings your rooms, and every card shows the
  camera with an **Answer** button ([details](communication.md#answer-the-door)).
- **Siri to any speaker** — "Hey Siri, run RoomCall Basement", no HomePod needed
  ([Siri](siri-announce.md)), or a talk button in the [Apple Home app](apple-home.md).
- **Reminders that wait until it's done** — "The laundry is done" repeats until someone taps
  *Took out the dry clothes* ([announcements and tasks](announcements.md)).
- **Talk back** — a **Reply** button on the call notification, or a small Assist satellite
  that listens for a spoken answer ([talk-back](communication.md#hands-free-talk-back)).

Using Sonos? See [Sonos intercom](sonos-intercom.md). Comparing intercom options?
See [Home Assistant intercom](home-assistant-intercom.md).

## Also included

- Do Not Disturb, quiet hours and **Urgent** calls.
- Your own chime, call history with replay, and transcripts with a speech-to-text engine.
- Simple, standard, compact and kiosk card layouts; Glass, Material or your dashboard theme.
- Receivers and TV boxes woken for the call and put back afterwards, where the device allows it.
- `roomcall.broadcast`, a notify entity per room, and [blueprints](blueprints.md).
- A sideloadable [iPhone app](native-ios.md), and tools for [Assist and AI agents](ai.md).
- Local audio: your phone → Home Assistant → the speaker.

## Try the standalone app in 30 seconds

No Home Assistant? RoomCall also runs as its own server with mDNS speaker discovery.

```bash
pipx install "roomcall @ https://github.com/daredoole/RoomCall/releases/download/v0.21.0/roomcall-0.21.0-py3-none-any.whl"
roomcall serve --demo
```

The package is available from GitHub releases, not currently from PyPI. Install
[speaker extras](getting-started.md#install) for real devices.

Open <http://localhost:8765>, pick a room and hold the button. Demo mode uses fake speakers, so
nothing is played.

![Standalone web app](assets/screenshot-desktop.png)
