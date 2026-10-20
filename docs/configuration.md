# Configuration

RoomCall works with no config file. Create `roomcall.yaml` (or pass `-c path`) to customise it.

```yaml
host: 0.0.0.0
port: 8765
public_url: http://192.168.1.20:8765
token: change-me
volume: 0.4
gain_db: 3
discovery_timeout: 4
rooms:
  Downstairs: [Basement, Kitchen Hub]
  Upstairs: [Office]
speakers:
  - name: Garage HomePod
    kind: airplay
    host: 192.168.1.40
```

| Key | Default | Description |
|---|---|---|
| `host` | `0.0.0.0` | Address to listen on. |
| `port` | `8765` | HTTP port. |
| `public_url` | auto | Base URL speakers use to download messages. Must be reachable from the speakers' network. |
| `token` | none | When set, `/api/*` requires header `X-RoomCall-Token` (or `?token=`). Open the web app as `/?token=…`. |
| `volume` | unchanged | Fixed announcement volume, `0.0`–`1.0`. Per-request `?volume=` overrides it. |
| `gain_db` | `0` | Boost or cut recordings before sending (quiet phone mics: try `3`–`6`). |
| `discovery_timeout` | `4` | Seconds to listen for mDNS answers. |
| `rooms` | none | Room name → list of speaker names or ids. Unlisted speakers are their own room. |
| `speakers` | none | Speakers mDNS cannot see. `kind` is `sonos`, `airplay` or `cast`. |

!!! tip "Matching is forgiving"
    Room members match speaker names case-insensitively, ignoring `-`, `_` and extra spaces,
    so `kitchen-hub` matches **Kitchen Hub**.
