# Home Assistant

RoomCall is a native Home Assistant integration. It runs **inside** Home Assistant, so there is no
separate server: your speakers are the media players Home Assistant already controls, and your
rooms are Home Assistant areas.

![RoomCall card on a desktop dashboard](assets/ha-dashboard-desktop.png)

## Before you start

- **Home Assistant 2025.6** or newer, with [HACS](https://hacs.xyz/docs/use/) installed.
- **HTTPS or the companion app** for the microphone. Browsers only allow the mic on secure pages,
  so open Home Assistant through Nabu Casa, a reverse proxy, Tailscale, or the mobile app.
- **A reachable internal URL.** Speakers download each clip from Home Assistant. Check
  *Settings → System → Network → Local network* is an address your speakers can reach
  (for example `http://192.168.1.10:8123`).

## 1. Install

=== "HACS (one click)"

    [![Open your Home Assistant instance and open RoomCall in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=daredoole&repository=RoomCall&category=integration)

    Click **Download**, then restart Home Assistant.

=== "HACS (manual)"

    1. HACS → ⋮ → **Custom repositories**.
    2. Add `https://github.com/daredoole/RoomCall` as type **Integration**.
    3. Search **RoomCall** → **Download** → restart Home Assistant.

=== "Without HACS"

    Download `roomcall.zip` from the
    [latest release](https://github.com/daredoole/RoomCall/releases/latest), unzip it into
    `/config/custom_components/roomcall/` and restart Home Assistant.

## 2. Add the integration

[![Open your Home Assistant instance and start setting up RoomCall.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=roomcall)

Or go to **Settings → Devices & services → Add integration → RoomCall**.

Pick your speakers. Everything else is under **Configure** on the integration:

| Option | Default | What it does |
|---|---|---|
| Speakers | announce-capable players | Media players that make up the intercom |
| Announcement volume | speaker's current volume | Fixed volume for calls (Sonos) |
| Microphone boost | 0 dB | Raise if phone recordings sound quiet |
| Text-to-speech engine | your default | Used by `roomcall.broadcast` messages |
| **Chime** → chime | Walkie-talkie chirp | Chirp, ding, double beep, rising tones, doorbell, or none |
| **Chime** → custom file or URL | — | Your own sound, e.g. `/config/www/chime.mp3`; overrides the chime |
| **Delivery** → delay between speakers | 250 ms | Stops several Sonos in one home from dropping the same announcement |
| **Delivery** → retry | on | Tries a failed speaker once more after a second |
| **Delivery** → urgent call volume | 0.8 | Volume for urgent calls |
| **Quiet hours** → start / end / volume / rooms to skip | off | Quieter calls at night that skip bedrooms |
| **Notifications and replies** → phones | none | Phones notified about each call (not the caller's own) |
| **Notifications and replies** → dashboard | — | Path of the dashboard with the card; adds a **Reply** button |
| **Notifications and replies** → speech-to-text | none | Adds what was said to notifications and history |

!!! tip "One entity per speaker"
    A physical speaker often has several media players (native Sonos plus Music Assistant, Cast
    plus Android TV…). Pick one per speaker so messages do not play twice. RoomCall suggests
    announce-capable native entities and skips Music Assistant mirrors by default.

!!! info "Rooms come from areas"
    Each speaker's room is its entity area, or its device area. Speakers without an area only
    play when you talk to **Everywhere** — assign them in *Settings → Areas*.

## 3. Add the push-to-talk card

The integration registers the card automatically — no dashboard resource needed. Edit a
dashboard → **Add card** → **RoomCall** and set it up in the visual editor, or paste:

```yaml
type: custom:roomcall-card
title: Intercom
# optional:
room: kitchen                  # where this card is; replies come back here
default_room: basement         # room preselected
rooms: [kitchen, living_room]  # limit the room buttons
people: true                   # person buttons (true, false, or a list of person entities)
history: 3                     # recent calls to show (0 to hide)
layout: default                # default, compact or kiosk
live: true                     # stream while you talk (false: record, then send)
```

<div class="grid" markdown>
![Pick a room or a person](assets/ha-card-mobile.png){ width="240" }
![Hold to talk](assets/ha-card-talking.png){ width="240" }
![Delivered](assets/ha-card-delivered.png){ width="240" }
</div>

Hold the button and talk. With **live calls** (the default) the speakers start playing about a
second after you press, while you are still talking, and stop when you let go. Turn it off with
`live: false` to record the whole message first and then send it. Either way the card reports
which speakers played it. If your microphone records silence (wrong input
selected), the card says so instead of sending an empty clip.

- **Urgent** (top right) plays the next call at the urgent volume and ignores Do Not Disturb
  and quiet hours.
- **Bell** toggles **Do Not Disturb**: only urgent calls play while it's on.
- **People** buttons call whoever you tap in the room they're in (see [Call a person](#call-a-person)).
- **Recent calls** can be replayed in the browser, and **Reply** aims the card back at the caller.

### Layouts

<div class="grid" markdown>
![Compact](assets/ha-card-compact.png){ width="240" }
![Kiosk for a wall tablet](assets/ha-card-kiosk.png){ width="240" }
![Reply mode](assets/ha-card-reply.png){ width="240" }
</div>

`compact` fits a single row of a dashboard. `kiosk` is a big button for a wall tablet.

## Replies

With phones and a dashboard path set, every call sends a notification such as
**Kitchen → Basement: "dinner's ready"** with a **Reply** button. Tapping it opens your dashboard
with the card already aimed back at the caller's room, so you just hold and talk. The caller's own
phones are skipped.

For replies to know where a call came from, set `room:` on each card (for example the kitchen
tablet's card gets `room: kitchen`).

!!! note "Why the speaker can't hear your reply"
    Sonos, HomePod and Nest speaker microphones are reserved for their own voice assistants, so
    replies come from a phone or tablet. Mic stations next to each speaker are on the
    [roadmap](roadmap.md).

## AV receivers and TV boxes

Speakers that support *announce* (Sonos, Google/Nest, HomePod) mix the call over your music.
Others — AV receivers through HEOS, Android TV / Google TV boxes such as onn through Cast — have
to stop what they're doing to play the call. RoomCall puts them back afterwards:

| Before the call | After the call |
|---|---|
| Receiver on the TV input | Switched back to the TV input, same volume |
| Receiver off | Turned back off |
| TV box in Netflix, YouTube… | The same app reopened (where you were in it is up to the app) |

RoomCall finds the receiver's input and the box's foreground app through the other media players
for the same device model in the same room: the receiver's `denonavr` entity and the box's
**Android TV Remote** entity. Add both integrations for the best result, and pick the HEOS entity
(receiver) or Cast entity (TV box) as the RoomCall speaker.

!!! tip "Receiver plus TV box in one room"
    If the TV box plays through the receiver, add only the receiver as a speaker. The call then
    interrupts the sound for a few seconds while the picture keeps going.

Turn this off under Configure → Delivery → *Put speakers back after a call*.

## Do Not Disturb and quiet hours

- `switch.roomcall_do_not_disturb`: while on, normal calls are refused and urgent calls still play.
  Turn it on in your bedtime routine.
- **Quiet hours** (Configure → Quiet hours): calls play at the quiet volume and skip the rooms you
  pick (for example the kids' rooms). The card tells you which rooms were skipped.

## Call a person

`people: [person.alex]` in the action, or a person button on the card, plays wherever that
person is right now. RoomCall follows the person's device trackers to a room-presence sensor
(any `sensor.*_area` on the same device, as [Bermuda](https://github.com/agittins/bermuda)
creates), or uses a tracker or sensor whose state is an area name. If it can't tell where they
are, the call is refused instead of playing in the wrong room.

## `roomcall.broadcast` action

Speak a message (text-to-speech) or play an audio URL in rooms or wherever people are:

```yaml
action: roomcall.broadcast
data:
  areas: [kitchen, basement]   # omit for every RoomCall speaker
  message: Dinner is ready
  volume: 0.4                  # optional, Sonos
```

```yaml
action: roomcall.broadcast
data:
  people: [person.alex]
  message: Your ride is here
  priority: urgent             # louder, ignores Do Not Disturb and quiet hours
```

```yaml
action: roomcall.broadcast
data:
  areas: garage
  media_url: http://homeassistant.local:8123/local/chime.mp3
```

With [Chime TTS](https://github.com/nimroddolev/chime_tts) installed, messages get the same chime
as push-to-talk. The action returns which speakers played the message, so scripts can react:

```yaml
- action: roomcall.broadcast
  data: { areas: basement, message: "Laundry is done" }
  response_variable: call
- if: "{{ not call.ok }}"
  then:
    - action: notify.mobile_app_phone
      data: { message: "Basement speaker missed the laundry call" }
```

Siri, Apple Watch and CarPlay can call this too — see [Siri and Shortcuts](siri-shortcuts.md).

## Entities

- `notify.roomcall_<room>`: one per room — `notify.send_message` speaks the message there
  (see [Blueprints](blueprints.md#room-notify-entities)).
- `sensor.roomcall_last_call`: time of the last call, with attributes `target`, `from`, `kind`
  (`talk`, `message`, `media`), `priority`, `delivered`, `failed`, `skipped`, `text`, and
  `history` (the last 10 calls).
- `switch.roomcall_do_not_disturb`: see above.
- Per announcement (its own device, *Announcement: name*): `button.<name>_play_now` and
  `switch.<name>_enabled`, whose attributes describe the schedule or trigger, targets,
  `last_played` and `last_error`. See [Announcements](announcements.md).

AI agents and voice assistants: see [Managing RoomCall with AI](ai.md) — the `RoomCallAnnounce`
Assist intent, every action and field, and how announcements are created through the API.

## Diagnostics and Repairs

- **Repairs** (Settings → System → Repairs) flag speakers that are unavailable, a missing local
  network URL, or an HTTPS internal URL that speakers may refuse.
- **Download diagnostics** on the RoomCall integration for bug reports. What people said
  (messages and transcripts) and phone names are removed.

## How playback works

**Live calls** (the default) stream from the card over Home Assistant's WebSocket: ffmpeg encodes
the audio to MP3 as it arrives (chime first) and speakers play one growing URL, so they start
while you are still talking. **Recorded calls** (`live: false`, typed messages and automations)
are converted to MP3 in one go and kept in memory for two minutes behind an unguessable URL.
Either way each speaker gets `media_player.play_media`, a short moment apart. Speakers that support it get `announce: true`,
so Sonos lowers the music instead of stopping it.

Uploads, history and replays require a signed-in Home Assistant user. Clip and chime URLs are
unauthenticated because speakers cannot sign in, which is why clips are random and short-lived.
The last 20 calls are kept in memory for 24 hours for replay; they are not written to disk.

!!! warning "Keep the internal URL on plain http"
    Speakers download clips from Home Assistant's internal URL. If you put Home Assistant behind
    a reverse proxy with a home-made certificate for the microphone, leave the **internal URL** on
    `http://<ip>:8123`: Sonos and Cast speakers can't trust a home-made certificate.
