<p align="center">
  <img src="docs/assets/logo.png" alt="RoomCall logo" width="96">
</p>

<h1 align="center">RoomCall</h1>

<p align="center">
  <b>Push-to-talk intercom for Home Assistant, on the speakers you already own.</b><br>
  Hold a button, talk, and your voice plays in the room you picked — Sonos, AirPlay and Google Cast.
</p>

<p align="center">
  <a href="https://my.home-assistant.io/redirect/hacs_repository/?owner=daredoole&repository=RoomCall&category=integration"><img src="https://my.home-assistant.io/badges/hacs_repository.svg" alt="Open your Home Assistant instance and open RoomCall in HACS"></a>
</p>

<p align="center">
  <a href="https://hacs.xyz"><img src="https://img.shields.io/badge/HACS-Custom-41BDF5.svg" alt="HACS Custom"></a>
  <a href="https://github.com/daredoole/RoomCall/releases"><img src="https://img.shields.io/github/v/release/daredoole/RoomCall" alt="Latest release"></a>
  <a href="https://github.com/daredoole/RoomCall/actions/workflows/ci.yml"><img src="https://github.com/daredoole/RoomCall/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/daredoole/RoomCall/actions/workflows/validate.yml"><img src="https://github.com/daredoole/RoomCall/actions/workflows/validate.yml/badge.svg" alt="HACS and hassfest validation"></a>
  <a href="https://daredoole.github.io/RoomCall/"><img src="https://img.shields.io/badge/docs-online-4f8cff" alt="Documentation"></a>
  <a href="LICENSE"><img src="https://img.shields.io/github/license/daredoole/RoomCall" alt="MIT license"></a>
  <a href="https://github.com/sponsors/daredoole"><img src="https://img.shields.io/github/sponsors/daredoole?logo=githubsponsors&label=Sponsor&color=EA4AAA" alt="GitHub Sponsors"></a>
  <a href="https://buymeacoffee.com/daredoole"><img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-FFDD00?logo=buymeacoffee&logoColor=black" alt="Buy me a coffee"></a>
</p>

<p align="center">
  <img src="docs/assets/ha-card-mobile.png" alt="RoomCall card: pick a room and hold to talk" width="250">
  &nbsp;
  <img src="docs/assets/ha-card-talking.png" alt="RoomCall card while talking" width="250">
  &nbsp;
  <img src="docs/assets/ha-card-delivered.png" alt="RoomCall card after delivery" width="250">
</p>

---

## Why RoomCall?

Apple's Intercom only works on HomePods, Google's Broadcast only on Google speakers, and Sonos has
no intercom at all. RoomCall turns **every speaker Home Assistant already controls** into one
intercom, organised by your Home Assistant **areas**.

- 🎙️ **Hold to talk** from the Home Assistant app or any browser — a dashboard card, no extra app.
- 🏠 **Rooms are your areas** — talk to one room or the whole house.
- 📻 **Walkie-talkie chirp** before every message so people know a call is coming.
- 🔉 **Announce mode** — Sonos lowers the music for your message instead of stopping it.
- 🗣️ **Automations too** — `roomcall.broadcast` speaks text (TTS) or plays audio in any area.
- 🔒 **Local only** — audio goes from your phone to Home Assistant to the speaker. No cloud, no account.

## Requirements

| | |
|---|---|
| Home Assistant | 2025.6 or newer |
| Speakers | Any `media_player` that can play an MP3 URL: Sonos, Google Cast / Nest, AirPlay, … |
| Microphone | Open the dashboard over **HTTPS** (Nabu Casa, a reverse proxy, Tailscale) or in the Home Assistant mobile app. Browsers block the mic on plain `http://`. |
| Optional | [Chime TTS](https://github.com/nimroddolev/chime_tts) — typed messages then get the chirp too |

## Install

### 1. Download with HACS (recommended)

Click the button, then **Download**, then restart Home Assistant:

[![Open your Home Assistant instance and open RoomCall in HACS.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=daredoole&repository=RoomCall&category=integration)

<details>
<summary>No button? Add it manually</summary>

1. HACS → ⋮ (top right) → **Custom repositories**.
2. Repository `https://github.com/daredoole/RoomCall`, type **Integration** → **Add**.
3. Search for **RoomCall** → **Download** → restart Home Assistant.

</details>

<details>
<summary>Without HACS</summary>

Download `roomcall.zip` from the [latest release](https://github.com/daredoole/RoomCall/releases/latest),
unzip it into `/config/custom_components/roomcall/` and restart Home Assistant.

</details>

### 2. Add the integration

[![Open your Home Assistant instance and start setting up RoomCall.](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=roomcall)

Pick your speakers — RoomCall pre-selects the ones that support announcements. Choose **one entity
per physical speaker** so messages don't play twice.

### 3. Add the card

Edit a dashboard → **Add card** → search **RoomCall**, or paste:

```yaml
type: custom:roomcall-card
title: Intercom
```

That's it — no resources to register, the integration loads the card for you.

<p align="center">
  <img src="docs/assets/ha-dashboard-desktop.png" alt="RoomCall card on a desktop dashboard" width="820">
</p>

## Use it in automations

```yaml
action: roomcall.broadcast
data:
  areas: [kitchen, basement]   # leave out for every RoomCall speaker
  message: Dinner is ready!
  volume: 0.4                  # optional
```

Play an audio file instead of text with `media_url:`. The action returns which speakers played
the message, and `sensor.roomcall_last_call` records every call. More examples in the
[Home Assistant guide](https://daredoole.github.io/RoomCall/home-assistant/).

## Options

**Settings → Devices & services → RoomCall → Configure**

| Option | Default | What it does |
|---|---|---|
| Speakers | announce-capable players | Which media players are part of the intercom |
| Announcement volume | speaker's current volume | Fixed volume for calls (Sonos) |
| Play a chime first | on | Walkie-talkie chirp before each message |
| Microphone boost | 0 dB | Raise if phone recordings sound quiet |
| Text-to-speech engine | your default | Used for typed messages |

## How it works

```mermaid
sequenceDiagram
    participant P as Phone (card)
    participant H as Home Assistant
    participant S as Speaker
    P->>H: hold-to-talk recording (HTTPS, authenticated)
    H->>H: ffmpeg → MP3 + chirp, kept 2 min under a random URL
    H->>S: media_player.play_media (announce)
    S->>H: download clip from the internal URL
    H-->>P: delivery report on the card
```

A call plays about 1–2 seconds after you let go (AirPlay adds ~2 s of buffering).

## FAQ

<details>
<summary><b>Can people talk back through the speaker?</b></summary>

Not through Sonos, HomePod or Nest speakers — their microphones are reserved for their own voice
assistants. Talk-back needs a device RoomCall can listen to (a phone or tablet, or a Home Assistant
voice satellite); it's next on the [roadmap](https://daredoole.github.io/RoomCall/roadmap/).
</details>

<details>
<summary><b>The card says "Microphone blocked".</b></summary>

Open Home Assistant over HTTPS (or use the companion app) and allow the microphone when asked.
See [Troubleshooting](https://daredoole.github.io/RoomCall/troubleshooting/).
</details>

<details>
<summary><b>It says "Delivered" but I hear nothing.</b></summary>

The speaker couldn't download the clip. Set **Settings → System → Network → Local network** to an
address your speakers can reach, e.g. `http://192.168.1.10:8123`.
</details>

## Standalone server (no Home Assistant)

RoomCall also runs on its own, discovering speakers over mDNS and serving a web app:

```bash
pipx install "roomcall[all]"
roomcall serve        # http://<this-machine>:8765
```

See [Getting started](https://daredoole.github.io/RoomCall/getting-started/).

## Support the project

RoomCall is free and MIT-licensed. If it's useful in your home, you can sponsor its development:

<a href="https://github.com/sponsors/daredoole"><img src="https://img.shields.io/badge/Sponsor%20on%20GitHub-EA4AAA?style=for-the-badge&logo=githubsponsors&logoColor=white" alt="Sponsor on GitHub" height="42"></a>
&nbsp;
<a href="https://buymeacoffee.com/daredoole"><img src="https://img.buymeacoffee.com/button-api/?text=Buy%20me%20a%20coffee&emoji=%E2%98%95&slug=daredoole&button_colour=FFDD00&font_colour=000000&font_family=Inter&outline_colour=000000&coffee_colour=ffffff" alt="Buy me a coffee" height="42"></a>

Sponsorship never buys feature priority, private support or early access to security fixes.

## Contributing

Bug reports, speaker compatibility notes and pull requests are welcome — see
[CONTRIBUTING.md](CONTRIBUTING.md) and the [Code of Conduct](CODE_OF_CONDUCT.md).
Security issues: please follow [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE)
