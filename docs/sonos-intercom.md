---
title: Sonos intercom with Home Assistant
description: Use your Sonos speakers as a push-to-talk intercom. Hold a button in Home Assistant, talk, and your voice plays in the room you picked. Free, open source, local audio.
---

# Sonos intercom with Home Assistant

Sonos speakers have no intercom of their own, and Apple's Intercom works only on HomePods.
RoomCall adds one through Home Assistant: hold a button on your dashboard, talk, and your
voice plays on the Sonos speakers in the room you picked, with a short chime first.

![Pick a room and hold to talk](assets/ha-card-mobile.png){ width="260" align=right }

## How it sounds on Sonos

- **Over the music, not instead of it.** Where a speaker reports announcement support,
  RoomCall sends the call as an announcement, so Sonos lowers the music instead of stopping it.
- **Live.** By default your voice streams while you hold the button; the speaker's startup
  and buffering decide when playback actually begins.
- **Whole-house calls.** Several Sonos speakers in one household can drop an announcement
  that arrives at the same moment as another, so RoomCall starts each speaker 250 ms after
  the previous one and retries a speaker that fails once. Both are under
  *Configure → Delivery*.
- **A fixed call volume** if you want one, under *Configure → Announcement volume*.

## Set it up in three steps

1. **Install RoomCall** from the Marketplace, or HACS before Home Assistant 2026.11, and
   restart if asked
   ([install options](home-assistant.md#1-install)).
2. **Add the RoomCall integration** and pick your Sonos media players — one entity per
   speaker, so a call doesn't play twice ([details](home-assistant.md#2-add-the-integration)).
3. **Add the RoomCall card** to a dashboard, open it in the Home Assistant app (or over
   HTTPS), pick a room and hold to talk ([card options](home-assistant.md#3-add-the-push-to-talk-card)).

Rooms are your Home Assistant areas, so put each Sonos speaker in its area first.

## Limits

- **Sonos microphones stay with Sonos.** They can't be used to answer back. Reply from
  your phone's notification, the card, or a small
  [Assist satellite with hands-free talk-back](communication.md#hands-free-talk-back).
- **Speakers fetch each clip from Home Assistant.** Its internal URL must be reachable from
  the speakers and should stay on plain `http://`; Sonos can't trust a home-made certificate
  ([troubleshooting](troubleshooting.md#speakers-report-success-but-stay-silent)).
- **"Accepted" is not "heard".** The card reports that each speaker accepted the request,
  not that someone heard it. Use *Settings → Setup and sound tests* on the card to confirm
  each room once.
- The microphone needs HTTPS or the Home Assistant app; browsers block it on plain `http://`.

[Full Home Assistant guide →](home-assistant.md){ .md-button .md-button--primary }
[Home Assistant intercom overview](home-assistant-intercom.md){ .md-button }
