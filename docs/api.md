# HTTP API

All `/api/*` endpoints require `X-RoomCall-Token` when a `token` is configured.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/status` | Version, speaker count, clip base URL |
| `GET` | `/api/speakers` | Discovered speakers with their room |
| `GET` | `/api/rooms` | Rooms and their speaker names |
| `POST` | `/api/refresh` | Re-run discovery |
| `POST` | `/api/talk?target=<room\|speaker\|all>&volume=0.4` | Body: audio (`audio/webm`, `audio/mp4`, `audio/ogg`, `audio/mpeg`, `audio/wav`) |
| `GET` | `/clips/<id>.mp3` | Clip download used by speakers (no token; ids are random and expire) |

## `POST /api/talk`

```bash
curl -X POST -H "Content-Type: audio/mpeg" --data-binary @hello.mp3 \
  "http://localhost:8765/api/talk?target=Kitchen"
```

```json
{
  "target": "Kitchen",
  "ok": true,
  "clip": "http://192.168.1.20:8765/clips/Qm3k…mp3",
  "results": [
    {"speaker": "Kitchen Speaker", "ok": true, "elapsed_ms": 1180, "error": null},
    {"speaker": "Kitchen Hub", "ok": true, "elapsed_ms": 1544, "error": null}
  ]
}
```

| Status | Meaning |
|---|---|
| `200` | Every speaker accepted the clip |
| `207` | Some speakers failed — see `results` |
| `404` | No speaker matches `target` |
| `422` | The audio could not be converted |

## CLI

```bash
roomcall send Kitchen hello.mp3 --url http://localhost:8765
```
