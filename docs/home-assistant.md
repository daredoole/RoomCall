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

| Option | Default | What it does |
|---|---|---|
| Speakers | announce-capable players | Media players that make up the intercom |
| Announcement volume | speaker's current volume | Fixed volume for calls (Sonos) |
| Play a chime first | on | Walkie-talkie chirp before each message |
| Microphone boost | 0 dB | Raise if phone recordings sound quiet |
| Text-to-speech engine | your default | Used by `roomcall.broadcast` messages |

Change any of these later with **Configure** on the integration.

!!! tip "One entity per speaker"
    A physical speaker often has several media players (native Sonos plus Music Assistant, Cast
    plus Android TV…). Pick one per speaker so messages do not play twice. RoomCall suggests
    announce-capable native entities and skips Music Assistant mirrors by default.

!!! info "Rooms come from areas"
    Each speaker's room is its entity area, or its device area. Speakers without an area only
    play when you talk to **Everywhere** — assign them in *Settings → Areas*.

## 3. Add the push-to-talk card

The integration registers the card automatically — no dashboard resource needed. Edit a
dashboard → **Add card** → **RoomCall**, or paste:

```yaml
type: custom:roomcall-card
title: Intercom
# optional:
default_room: kitchen          # area id preselected
rooms: [kitchen, living_room]  # limit the room buttons
```

<div class="grid" markdown>
![Pick a room](assets/ha-card-mobile.png){ width="240" }
![Hold to talk](assets/ha-card-talking.png){ width="240" }
![Delivered](assets/ha-card-delivered.png){ width="240" }
</div>

Hold the button, talk, release. The card plays a short chirp then your message in the chosen
room and reports which speakers played it. If your microphone records silence (wrong input
selected), the card says so instead of sending an empty clip.

## `roomcall.broadcast` action

Speak a message (text-to-speech) or play an audio URL in one or more areas:

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
  areas: garage
  media_url: http://homeassistant.local:8123/local/chime.mp3
```

With [Chime TTS](https://github.com/nimroddolev/chime_tts) installed, messages get the same chirp
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

## Sensor

`sensor.roomcall_last_call` holds the time of the last broadcast, with attributes `target`,
`kind` (`talk`, `message`, `media`), `delivered` and `failed`.

## How playback works

Recordings are converted to MP3 with Home Assistant's ffmpeg (with the chirp in front), kept in
memory for two minutes behind an unguessable URL, and handed to each speaker with
`media_player.play_media`. Speakers that support it get `announce: true`, so Sonos lowers the
music instead of stopping it. Uploads require a logged-in Home Assistant user; the clip URL is
unauthenticated because speakers cannot log in, which is why it is random and short-lived.
