# Managing RoomCall with AI

RoomCall is built so an AI agent can set it up and run it on any Home Assistant — through
Home Assistant's own APIs, with no RoomCall-specific credentials. This page is the reference an
agent needs. A machine-readable summary is at [`/llms.txt`](llms.txt).

## 1. Talk through Assist (any LLM conversation agent)

RoomCall registers the **`RoomCallAnnounce`** intent. Home Assistant offers registered intents to
every LLM-based conversation agent (OpenAI, Anthropic, Google, Ollama…) as a tool, so once
RoomCall is installed, users can just say *"tell the kitchen dinner is ready"*.

| Slot | Required | Meaning |
|---|---|---|
| `message` | yes | What to say |
| `area` | no | Room name, id or alias; everywhere when omitted |
| `urgent` | no | Overrides Do Not Disturb and quiet hours |

## 2. Speak now: `roomcall.broadcast`

```yaml
action: roomcall.broadcast
data:
  areas: [kitchen]                 # area ids or names
  speakers: [media_player.office]  # specific media players
  people: [person.alex]            # wherever they are (room presence)
  message: Dinner is ready         # or media_url: http://…/clip.mp3
  priority: normal                 # or urgent
  volume: 0.4                      # optional
```

It **returns** `{ok, id, results: [{entity_id, speaker, ok, error, attempts}], skipped}`, so an
agent can confirm delivery (`return_response: true` / `response_variable:`). Errors are
`ServiceValidationError`s with a plain-language reason, for example *"Do Not Disturb is on. Send
it as urgent to override."*

## 3. Create, change and delete announcements

Announcements are **config subentries** of the RoomCall entry, types `scheduled` and
`triggered`. Create one with the standard subentry flow (REST shown; the WebSocket and the
Home Assistant MCP server's `config_subentry` helper work the same way):

```http
POST /api/config/config_entries/subentries/flow
{"handler": ["<roomcall_entry_id>", "scheduled"]}

POST /api/config/config_entries/subentries/flow/<flow_id>
{
  "name": "School bus",
  "time": "07:30:00",
  "days": ["mon", "tue", "wed", "thu", "fri"],
  "message": "Bus in 10 minutes",
  "rooms": ["kitchen"],
  "target_speakers": [],
  "people": [],
  "priority": "normal"
}
```

```json
{
  "name": "Garage left open",
  "entity": "cover.garage_door",
  "to_state": "open",
  "for": {"minutes": 10},
  "cooldown": 600,
  "message": "The garage door has been open for 10 minutes",
  "rooms": [],
  "priority": "urgent"
}
```

| Field | Types | Notes |
|---|---|---|
| `name` | both | Title; becomes the device name `Announcement: <name>` |
| `message` | both | Template. Triggered adds `name`, `state`, `from_state`, `entity` |
| `rooms`, `target_speakers`, `people` | both | All empty = everywhere |
| `priority` | both | `normal` or `urgent` |
| `time`, `days` | scheduled | `HH:MM:SS`; `mon`…`sun` |
| `entity`, `to_state`, `for`, `cooldown` | triggered | `for` is a duration dict; `cooldown` seconds |

- **List:** WebSocket `config_entries/subentries/list` with `entry_id`.
- **Change:** start a `reconfigure` subentry flow (same fields).
- **Delete:** WebSocket `config_entries/subentries/delete` with `entry_id` and `subentry_id`.

Saving any of these reloads RoomCall automatically.

## 4. Read state

| Entity | What it tells you |
|---|---|
| `sensor.roomcall_last_call` | Last call; attribute `history` has the last 10 (target, from, ok, delivered, failed, skipped, text) |
| `switch.roomcall_do_not_disturb` | Do Not Disturb |
| `switch.<announcement>_enabled` | On/off, plus attributes: kind, message, targets, schedule or trigger, `last_played`, `last_error` |
| `button.<announcement>_play_now` | Press to test an announcement |
| `notify.roomcall_<room>` | `notify.send_message` speaks in that room |

## 5. Change settings

Speakers, chime, delivery, quiet hours, notifications, transcripts and Apple Home are the
RoomCall **options flow** (Configure). Sections are nested objects: `chime_options`, `delivery`,
`quiet_hours`, `replies`, `homekit`. Omitted sections keep their saved values.

## 6. Troubleshoot

- **Repairs** explain unavailable speakers and unusable internal URLs.
- **Diagnostics** (download on the integration) list speakers, rooms, announce support, recent
  calls and Apple Home status, with what people said redacted.
- A call that reports `ok` but stays silent usually means the speaker cannot fetch
  `http://<internal_url>/api/roomcall/…` — check the internal URL is plain `http://` and
  reachable from the speaker's network.
