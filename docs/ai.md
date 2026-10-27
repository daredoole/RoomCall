# Managing RoomCall with AI

RoomCall is built so an AI agent can set it up and run it on any Home Assistant — through
Home Assistant's own APIs, with no RoomCall-specific credentials. This page is the reference an
agent needs. A machine-readable summary is at [`/llms.txt`](llms.txt).

## Local voice commands and Echo calls

The built-in Home Assistant conversation agent recognizes “announce to Basement that
Dinner is ready” and “RoomCall Kitchen”. Announcements use the normal queue and policy.
A live-call command spoken at a paired TECHO5 Echo rings every browser and
saved native iOS station in the destination area; the first to press Answer takes the call.
Native app 0.24.0/build 5 uses Settings → My room and HA Companion notifications
that open its Answer screen, including when the app is closed. Live Activities
are not incoming-call notifications. Outside a paired Echo,
use **Settings → Room stations → Echo calls** to choose and call an Echo.
See [Echo pairing and live-call consent](communication.md#converted-echo-live-calls).
Do not report queued messages, returned PCM or device states as confirmed audibility.

## 1. Talk through Assist (any LLM conversation agent)

RoomCall explicitly contributes `roomcall__*` tools through Home Assistant's LLM
platform on current releases. On older supported releases, select the **RoomCall**
LLM API in your conversation agent's options. The `RoomCallAnnounce` intent remains
available for compatible Assist/intent workflows. Registering an intent alone is
not sufficient for tool discovery on current Home Assistant.

Expose the relevant media players, people and satellites to the assistant. Tools
also respect the caller's Home Assistant entity permissions. External clients can
use Home Assistant's MCP integration with the selected RoomCall API; no separate
RoomCall credentials or server are needed. API availability depends on the HA
version and MCP configuration.

Start with **`roomcall__catalog`**. For installation/setup, use
**`roomcall__setup`** and the [guided agent setup contract](agent-setup.md), including
bootstrap before an entry exists, persistent plans, tests, verification and undo.
Use **`roomcall__announcements`** for announcement administration and
**`roomcall__doctor`** for repair proposals. These three administration tools require
an authenticated HA administrator.

For daily messaging, use **`roomcall__discover`**, **`roomcall__preview`**, **`roomcall__send`**
and **`roomcall__status`**. Discover `speech_profiles` and permitted household
`profiles`; use `speech` to prepare text without playback, or `profile` on preview/send
to reuse household defaults. Use an explicit target and reuse the same
`idempotency_key` when retrying an unchanged message. `diagnose` explains policy and
capabilities; blocked messages must not be automatically promoted to urgent.
`ask` collects actual satellite responses. The complete contract is in
[Messages and room stations](communication.md).

For a dedicated non-administrator agent account, start with **`roomcall__policy`**
to discover its RoomCall restrictions. Policies can limit operations, destinations,
hours, urgency, requested volume and runtime message rate. They add to HA permissions;
they do not scope that account's unrelated HA APIs. A setup administrator configures
these policies under **Configure → Communication**. Accounts without a RoomCall
policy retain existing access rules.

For restart retention, send `durable: true` with an explicit idempotency key. Only
queued, unexpired work resumes with current permission/policy checks. An `interrupted`
result means dispatch may have happened; inspect it before sending a replacement.
Ordinary jobs remain memory-only. Check `status.progress` for measured stages and
subscribe to owned jobs with authenticated `roomcall/job_events`; an event is not
proof of audible playback. See [communication settings and lifecycle](communication.md).

The legacy intent has these slots:

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
agent can check request acceptance (`return_response: true` / `response_variable:`).
Success is not proof of audible playback; inspect the independent `evidence` and
explicit acknowledgment where available. Errors are
`ServiceValidationError`s with a plain-language reason, for example *"Do Not Disturb is on. Send
it as urgent to override."*

### Siri shortcut setup and exports

The catalog's `shortcut_exports` describes the authenticated download endpoint:
`POST /api/roomcall/shortcut`, with `room`, optional `language`, and `format`
(`native`, `native_signed`, `cherri`, `shortcut`, or `signed`). Discover exact room IDs first. Exports are
restricted to configured rooms and the caller's permitted speakers. Unknown IDs
return `no_targets`; they never fall back to Everywhere.

Native exports accept `room: "ask"` for a live picker, `multiple: true`, and
`message_mode: "ask"` or `"input"`. Discovery returns `shortcut_choices`, a
friendly-name-to-area-ID dictionary restricted to permitted destinations.
Native templates contain no Companion server IDs; configure server/action selectors
on import. Native signed exports require the same opt-in signing credentials.

Recommend the HA Companion **Perform Action** action for Siri. Its server must
be selected on the phone; use a Shortcuts Dictionary containing the exact room ID
and the Ask for Input message variable. Portable file exports use the URL handler
and require confirmation each time. Unsigned exports need signing before iPhone
import. Signed exports require the user's own HubSign key and explicit
`allow_remote: true`; do not persist the key. See [Siri setup](siri-shortcuts.md).

## 3. Create, change and delete announcements

Announcements are **config subentries** of the RoomCall entry, types `scheduled` and
`triggered`. Agents can use `roomcall__announcements` with `action: schema`, then
`create`, `list`, `update`, `enable`, `disable`, or confirmed `delete`. Updates
require the current revision; creates support persistent idempotency keys.
The native subentry flow remains available (REST shown; the WebSocket and the
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
| `task`, `remind_every` | both | Optional task someone must confirm (e.g. `Took out the dry clothes`); repeat every N minutes while open (0 = once) |

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
| `sensor.<announcement>_task` | Task announcements: `idle`, `open` or `done`, with `task`, `opened`, `done`, `done_by` |
| `button.<announcement>_mark_done` | Confirms the task was done (no effect when nothing is open) |

Use `roomcall__tasks` to list open tasks, `complete` one, or `snooze` it. Only complete a task
when a person says they did it; hearing or dismissing an alert is not doing the chore. Completion
fires `roomcall_task_completed` with `announcement_id`, `name`, `task`, `user_id` and `opened`.
A per-room `notify.roomcall_<room>` entity also accepts `notify.send_message` to speak there.

## 5. Change settings

Speakers, chime, delivery, quiet hours, notifications, transcripts and Apple Home are the
RoomCall **options flow** (Configure). Sections are nested objects: `chime_options`, `delivery`,
`quiet_hours`, `replies`, `homekit`, and `communication`. The communication section
includes `speech_profiles`, `household_profiles`, and `agent_policies`. Omitted sections
keep their saved values; use setup discovery's current `settings_schema` before editing.

## 6. Troubleshoot

- Playback endpoints expose an `interruption` plan. Use Cast for audio on Android
  TVs/boxes; Android TV Remote provides companion controls. HEOS receivers can use
  a verified native receiver control for power/input preparation. `catalog.playback_routes`
  describes explicit HDMI mappings under `communication.playback_routes`. Preview
  before sending; permissions/exposure must include every affected controller.
  `catalog.playback_delays` describes per-playback-entity startup and restore delays
  under `communication.playback_delays` (0–60 seconds). Denon controllers default to
  10 seconds after wake/input changes and a 2-second restore tail; explicit startup
  overrides apply before every send. Inspect `evidence[endpoint].playback_delays`
  for the delays applied to a call. HA reporting NET/on does not establish that the
  amplifier is audible. Discovery retains unavailable audio endpoints seen within
  24 hours, keeps saved selections editable, and marks Android TV Remote control-only.
  Inspect `evidence[endpoint].interruption.restore` after acceptance. Request completion
  and best-effort resumption do not prove audibility or recovery of an exact session.
- **Repairs** explain unavailable speakers and unusable internal URLs.
- **Diagnostics** (download on the integration) list speakers, rooms, announce support, recent
  calls and Apple Home status, with what people said redacted.
- `ok` establishes request acceptance, not audibility. Check the intended target,
  volume, queue and independent device evidence. For missing fetch evidence on
  RoomCall-hosted audio, check that the internal media URL is reachable from the
  speaker's network and uses a certificate it trusts (often plain HTTP on the LAN).
