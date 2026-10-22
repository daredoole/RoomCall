# Announcements

Let RoomCall speak on its own — at a time of day, or when something happens in your home.

**Settings → Devices & services → RoomCall → ⋮ → Add scheduled announcement / Add triggered
announcement.** Each announcement gets its own device with a **Play now** button and an
**Enabled** switch, so you can test it, pause it, or put it on a dashboard. Announcements with a
[task](#tasks-confirm-it-was-actually-done) also get a **Mark done** button and a **Task** sensor.

## Scheduled

Say something at a time of day, on the days you choose.

| Example | Time | Days | Message |
|---|---|---|---|
| School bus | 07:30 | Mon–Fri | `Bus in 10 minutes. Coats and backpacks!` |
| Morning briefing | 07:00 | Every day | `Good morning. It's {{ states('sensor.outdoor_temperature') }} degrees and {{ states('weather.home') }}.` |
| Bins | 19:00 | Wed | `Take the bins out tonight.` |

## Triggered

Say something when an entity changes — a light switch, a door, a motion sensor, a washer, a
person arriving. Optional **to this state** (`on`, `open`, `home`…), **for at least** (only if
it stays that way) and **cooldown** (don't repeat too often).

| Example | When this changes | To | For at least | Message |
|---|---|---|---|---|
| Porch light | `switch.porch_light` | `on` | — | `{{ name }} is {{ state }}.` |
| Garage left open | `cover.garage_door` | `open` | 10 min | `The garage door has been open for 10 minutes.` |
| Freezer warm | `binary_sensor.freezer_alarm` | `on` | 2 min | `Check the freezer.` (urgent) |

In triggered messages, `{{ name }}`, `{{ state }}`, `{{ from_state }}` and `{{ entity }}` describe
what changed.

## Tasks: confirm it was actually done

Some alerts aren't finished when someone hears them. Give an announcement a **task to confirm**,
for example `Took out the dry clothes`, and it stays open until someone says they did it:

| Example | When this changes | To | Task to confirm | Remind every |
|---|---|---|---|---|
| Laundry | `sensor.dryer` | `done` | `Took out the dry clothes` | 15 min |
| Dishwasher | `sensor.dishwasher` | `off` | `Emptied the dishwasher` | 30 min |
| Bins | scheduled, Wed 19:00 | — | `Bins are out` | 20 min |

- The announcement **repeats every _remind every_ minutes** (0 = once) while the task is open,
  with the same values as when it opened, and keeps working across restarts.
- Phones listed under **Notify** in RoomCall's options get one notification with two buttons: the
  task itself (**Took out the dry clothes**) and **Remind me later** (30 minutes). With people
  set, only their phones are told (everyone's, if none of them has the app). On Android the
  notification can't be swiped away; on iOS, swiping it away just leaves the reminders running.
- **Dismissing is never "done".** Only these mark a task done: the notification's task button,
  the **To do** list on the RoomCall card, the announcement's **Mark done** button, or the
  `tasks` operation. The person who confirmed it is recorded.

Each task announcement also gets a **Task** sensor (`idle`, `open`, `done`, with `opened`, `done`
and `done_by`) and fires `roomcall_task_completed` when done, so automations can react:

```yaml
triggers:
  - trigger: event
    event_type: roomcall_task_completed
    event_data:
      name: Laundry
actions:
  - action: notify.mobile_app_alex_phone
    data:
      message: "{{ trigger.event.data.task }} ✔"
```

A dryer door sensor can also confirm it for you: `button.press` on
`button.<announcement>_mark_done`.

## Where it plays

Every announcement can target **rooms**, **specific speakers**, and **people** (wherever they
are right now). Leave all three empty to play everywhere. **Urgent** announcements play at the
urgent volume and ignore Do Not Disturb and quiet hours.

## From automations and scripts

Anything RoomCall announcements do is also one action away:

```yaml
action: roomcall.broadcast
data:
  speakers: [media_player.kitchen]   # specific speakers, rooms (areas:) or people (people:)
  message: The dishwasher is done
```

Or press an announcement's button from any automation: `button.press` on
`button.<announcement>_play_now`.
