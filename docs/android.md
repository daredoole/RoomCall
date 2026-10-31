---
title: Use RoomCall on Android
description: Hold to talk from the Home Assistant app on Android, and send one-tap announcements from a widget, Quick Settings tile or Wear OS watch.
---

# Use RoomCall on Android

There is no separate RoomCall app for Android, and you don't need one. The
[Home Assistant Companion app](https://companion.home-assistant.io/) shows the RoomCall card,
and its widgets and tiles can send announcements.

| You want to | Use | Sends |
|---|---|---|
| Talk in your own voice | The card, in the Home Assistant app | Your voice |
| Open the card in one tap | A home-screen shortcut | Your voice |
| Send the same message with one tap | An Action button widget | Spoken text |
| Send it from the pull-down menu | A Quick Settings tile | Spoken text |
| Send it from your watch | A Wear OS Shortcuts tile | Spoken text |
| Say a new message out loud | Assist with an AI agent | Spoken text |

## Talk from the card

Add the RoomCall card to a dashboard ([setup guide](home-assistant.md)), open that dashboard in
the Home Assistant app, and allow the microphone when asked. Then pick a room and hold to talk.
The card only works while the app is open on that dashboard.

To get there in one tap, add a shortcut:

1. In the Home Assistant app, go to **Settings → Companion app → Manage Shortcuts**.
2. Enter a short label such as `Intercom`, and leave the type as **Dashboard**.
3. Enter the path of your dashboard, for example `/lovelace/intercom`.
4. Create the shortcut and add it to your home screen.

## Send a message with one tap

These use text-to-speech, so set up a text-to-speech engine first.

### Home-screen widget

1. Long-press an empty area of your home screen and open the widget list.
2. Under **Home Assistant**, add the **Action button** widget.
3. Choose the action `roomcall.broadcast`.
4. Enter the data, for example:

    ```yaml
    areas: [kitchen]
    message: Dinner is ready
    ```

5. Give it a name and icon, then save.

The widget doesn't work with Data Saver on, and background data must be allowed for the app.

### Quick Settings tile or watch

Tiles and the watch run a script rather than an action with its own data, so create the script
first. In **Settings → Automations & scenes → Scripts**, add one with this step:

```yaml
action: roomcall.broadcast
data:
  areas: [kitchen]
  message: Dinner is ready
```

- **Quick Settings tile:** go to **Settings → Companion app → Manage Tiles**, set a label, select
  your script and update the tile. Then edit your Quick Settings panel and drag the Home Assistant
  tile in.
- **Wear OS:** add the Home Assistant **Shortcuts** tile on your watch and choose your script.

## Say a new message

Home Assistant's **Assist** can send announcements if your conversation agent has RoomCall's tools
turned on ([AI guide](ai.md)). Then say something like *"tell the basement dinner is ready"*. The
Wear OS app has an Assist tile and complication too.

## Replies, tasks and the doorbell

Add your phone under **Notify** in RoomCall's options to get call notifications with **Reply**,
task buttons with **Remind me later**, and doorbell alerts with an **Answer** button. On Android, a
task notification can't be swiped away
([how tasks work](announcements.md#tasks-confirm-it-was-actually-done)).

## Limits

- **No background push-to-talk.** There's no lock-screen or hardware-button talk, because audio
  comes from the card while it's open.
- **Widgets and tiles send text, not your voice.** Your voice only comes from the card.
- **"Accepted" is not "heard".** RoomCall reports that the speakers accepted the announcement, not
  that someone heard it.

The widget, tile, shortcut and watch steps come from the
[Companion app documentation](https://companion.home-assistant.io/docs/integrations/android-widgets/),
which may change between app versions.

[Home Assistant guide →](home-assistant.md){ .md-button .md-button--primary }
[Troubleshooting](troubleshooting.md){ .md-button }
