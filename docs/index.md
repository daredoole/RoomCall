# RoomCall

**Push-to-talk intercom for Home Assistant, on the speakers you already own.** Hold a button,
talk, and your voice plays in the room you picked — on Sonos, AirPlay and Google Cast speakers.

[![Open your Home Assistant instance and open RoomCall in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=daredoole&repository=RoomCall&category=integration)

<div class="grid" markdown>
![Pick a room and hold to talk](assets/ha-card-mobile.png){ width="200" }
![Talking](assets/ha-card-talking.png){ width="200" }
![A task to confirm on the To do list](assets/ha-card-tasks.png){ width="200" }
![Settings panel](assets/ha-settings.png){ width="200" }
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
- **[Tasks you have to confirm](announcements.md#tasks-confirm-it-was-actually-done)** —
  "the laundry is done" keeps reminding until someone taps *Took out the dry clothes*;
  swiping the notification away doesn't count.
- **Talk, Type or Ask** — speak, type for text-to-speech, or ask an Assist satellite and get
  the answer back ([messages](communication.md)).
- **One Settings panel on the card** — guided setup and sound tests, Siri Shortcuts, room
  stations and voicemail ([tour](home-assistant.md#settings-panel)).
- **[Siri](siri-shortcuts.md)** — "Hey Siri, run RoomCall Basement", or one shortcut with a
  live room picker. The [native iOS app](native-ios.md) adds in-app talk, typed messages and tasks.
- **Reusable household profiles and prepared speech** — select saved destinations,
  message defaults and explicit voices; prepare/cache repeated speech without playback.
- **Durable jobs and delivery timeline** — opt into restart-safe queued messages and
  inspect preparation, dispatch, device evidence and explicit acknowledgment separately.
- **Scoped agent accounts** — restrict RoomCall operations, rooms, hours, urgency,
  requested volume and message rate; subscribe to owned-job updates.
- **Reply** from a phone notification, straight back to the caller's room.
- **Do Not Disturb, quiet hours and urgent calls.**
- **Call history** with replay and speech-to-text transcripts.
- **Works with receivers and TV boxes** — Denon/HEOS and Google TV go back to what they were doing.
- **Automations and AI** — `roomcall.broadcast`, a notify entity per room,
  [blueprints](blueprints.md), and explicit tools for Assist and MCP agents, including
  [guided setup](agent-setup.md) ([managing RoomCall with AI](ai.md)).
- **Local audio** — calls go from your phone to Home Assistant to the speaker. Optional
  speech, translation and shortcut signing use only providers you choose.

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
