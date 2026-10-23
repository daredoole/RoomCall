# Getting started

## Requirements

- A Linux machine, NAS, or Raspberry Pi on the **same network as your speakers**
  (or one that can reach them — see [Troubleshooting](troubleshooting.md#vlans)).
- Python 3.12+ and **ffmpeg** (converts browser recordings to MP3).
- A browser with a microphone. Browsers only allow the mic on **HTTPS or `localhost`**.

## Install

The standalone package is currently distributed as a wheel in GitHub releases,
not on PyPI. These commands install release **0.18.2** with its speaker extras:

=== "pipx"

    ```bash
    pipx install "roomcall[all] @ https://github.com/daredoole/RoomCall/releases/download/v0.18.2/roomcall-0.18.2-py3-none-any.whl"
    ```

=== "uv"

    ```bash
    uv tool install "roomcall[all] @ https://github.com/daredoole/RoomCall/releases/download/v0.18.2/roomcall-0.18.2-py3-none-any.whl"
    ```

=== "Only some speaker types"

    ```bash
    pipx install "roomcall[sonos,cast] @ https://github.com/daredoole/RoomCall/releases/download/v0.18.2/roomcall-0.18.2-py3-none-any.whl"
    ```

    Available extras: `sonos`, `airplay`, `cast` and `all`.

Install ffmpeg from your package manager (`sudo dnf install ffmpeg`, `sudo apt install ffmpeg`).

## 1. Check discovery

```console
$ roomcall discover
Basement           sonos    192.168.1.21    One
Office             sonos    192.168.1.22    Era 100
Living Room        airplay  192.168.1.23    HomePod mini
Kitchen Hub        cast     192.168.1.24    Google Nest Hub
Bedroom TV         cast     192.168.1.25    Chromecast with Google TV
```

If a device speaks several protocols (a Sonos also advertises AirPlay), RoomCall keeps the best one.

## 2. Start the server

```bash
roomcall serve
```

RoomCall prints the URL the speakers will download messages from. It must be reachable **from
the speakers** — set `public_url` in [the config](configuration.md) if auto-detection picks the
wrong interface.

## 3. Open it on your phone

Browsers block the microphone on plain `http://` LAN addresses. Pick one:

- **Reverse proxy (recommended):** add RoomCall to Caddy/Nginx/Traefik with a certificate trusted by the phone and
  open `https://roomcall.home.arpa`.
- **Home Assistant:** use the [HTTP API](api.md) from automations instead of the browser mic.
- **Testing:** open `http://localhost:8765` on the machine running RoomCall.

Add it to your home screen for an app-like, full-screen push-to-talk button.

## 4. Group speakers into rooms

Copy `roomcall.example.yaml` to `roomcall.yaml` and list room members — see
[Configuration](configuration.md).
