# Announcements

Let RoomCall speak on its own — at a time of day, or when something happens in your home.

**Settings → Devices & services → RoomCall → ⋮ → Add scheduled announcement / Add triggered
announcement.** Each announcement gets its own device with a **Play now** button and an
**Enabled** switch, so you can test it, pause it, or put it on a dashboard.

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
