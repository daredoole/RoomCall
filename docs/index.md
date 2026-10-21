# RoomCall

**Push-to-talk intercom for Home Assistant, on the speakers you already own.** Hold a button,
talk, and your voice plays in the room you picked — on Sonos, AirPlay and Google Cast speakers.

[![Open your Home Assistant instance and open RoomCall in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=daredoole&repository=RoomCall&category=integration)

<div class="grid" markdown>
![Pick a room and hold to talk](assets/ha-card-mobile.png){ width="240" }
![Talking](assets/ha-card-talking.png){ width="240" }
![Delivery report per speaker](assets/ha-card-delivered.png){ width="240" }
</div>

## What you get

- **Hold-to-talk card, live** — speakers play while you are still talking; set it up in the
  visual card editor, with compact and kiosk layouts.
- **Rooms are your areas** — talk to one room, everywhere, specific speakers, or a person
  wherever they are.
- **[Apple Home intercom](apple-home.md)** — every room is a camera tile with a talk button on
  iPhone, Apple Watch and Control Center.
- **[Announcements](announcements.md)** — scheduled ("bus in 10 minutes" on weekdays) and
  triggered ("the garage has been open 10 minutes"), with live values in the message.
- **Reply** from a phone notification, straight back to the caller's room.
- **Do Not Disturb, quiet hours and urgent calls.**
- **Call history** with replay and speech-to-text transcripts.
- **Works with receivers and TV boxes** — Denon/HEOS and Google TV go back to what they were doing.
- **Automations and AI** — `roomcall.broadcast`, a notify entity per room,
  [blueprints](blueprints.md), Siri Shortcuts, and an Assist intent for AI agents
  ([managing RoomCall with AI](ai.md)).
- **Local only** — no cloud, no accounts; audio never leaves your network.

[Set it up in Home Assistant →](home-assistant.md){ .md-button .md-button--primary }
[Run it without Home Assistant](getting-started.md){ .md-button }

## Try the standalone app in 30 seconds

No Home Assistant? RoomCall also runs as its own server with mDNS speaker discovery.

```bash
pipx install roomcall
roomcall serve --demo
```

Open <http://localhost:8765>, pick a room and hold the button. Demo mode uses fake speakers, so
nothing is played.

![Standalone web app](assets/screenshot-desktop.png)
