---
title: Announce to any speaker from Siri
description: Say “Hey Siri, run RoomCall Basement”, dictate a message, and Home Assistant speaks it on the Sonos, Cast or AirPlay speakers in that room. No HomePod needed.
---

# Announce to any speaker from Siri

Siri can announce to HomePods, but not to the Sonos, Google Cast or AirPlay speakers in the
rest of the house. With RoomCall and an iPhone shortcut, you say **"Hey Siri, run RoomCall
Basement"**, dictate the message, and Home Assistant speaks it in the basement through your
text-to-speech engine (for example Piper), with the RoomCall chime first.

![Room-specific Siri setup in the card's Settings](assets/ha-siri-shortcuts.png){ width="300" align=right }

## Set it up in three steps

1. **Install RoomCall in Home Assistant**, add the integration and pick your speakers
   ([Home Assistant guide](home-assistant.md)). Make sure a text-to-speech engine is set up.
2. **On the RoomCall card, open *Settings → Siri Shortcuts*** and choose the room. The panel
   shows the exact steps for the Shortcuts app: *Ask for Input* → *Dictionary* → Home
   Assistant *Perform Action* `roomcall.broadcast`, with your room's ID filled in.
3. **Name the shortcut "RoomCall Basement"**, run it once to grant permissions, then say
   **"Hey Siri, run RoomCall Basement."**

Want one shortcut that asks which room? The same panel builds a
[live room picker](siri-shortcuts.md#one-shortcut-with-a-live-room-picker).

## Limits

- **It's text-to-speech, not your voice.** Siri dictation becomes text, and Home Assistant
  speaks it. To send your own voice, hold to talk on the card, use the
  [Apple Home camera tile](apple-home.md), or the [iPhone app](native-ios.md).
- **Don't call the shortcut "Intercom".** Siri may run Apple's HomePod Intercom instead.
- **The shortcut runs on your iPhone** through the Home Assistant Companion app, so iOS
  may ask you to unlock the phone or confirm, depending on where you are.
- **Generated shortcut files need signing.** iPhones import only signed `.shortcut`
  files; building the shortcut by hand from the panel's steps needs no file at all
  ([options](siri-shortcuts.md#downloadable-shortcut-generator)).
- **"Accepted" is not "heard".** RoomCall reports that the speakers accepted the
  announcement, not that someone heard it.

[Full Siri and Shortcuts guide →](siri-shortcuts.md){ .md-button .md-button--primary }
[Home Assistant intercom](home-assistant-intercom.md){ .md-button }
