# Roadmap

Track progress and vote on the [GitHub issues](https://github.com/daredoole/RoomCall/issues).

## v0.1 — Push-to-talk ✅

- [x] Native Home Assistant integration: config flow, rooms from areas, `roomcall.broadcast`
- [x] Push-to-talk card, announce mode, walkie-talkie chirp, silent-mic detection
- [x] Standalone server with mDNS discovery of Sonos, AirPlay and Cast speakers

## v0.2 — A real intercom ✅

- [x] Reply from phone notifications ([#12](https://github.com/daredoole/RoomCall/issues/12))
- [x] Call a person wherever they are ([#16](https://github.com/daredoole/RoomCall/issues/16))
- [x] Do Not Disturb and quiet hours ([#17](https://github.com/daredoole/RoomCall/issues/17))
- [x] Urgent calls ([#18](https://github.com/daredoole/RoomCall/issues/18))
- [x] Call history with replay ([#19](https://github.com/daredoole/RoomCall/issues/19))
- [x] Speech-to-text transcripts ([#20](https://github.com/daredoole/RoomCall/issues/20))
- [x] Chime choice and custom chimes ([#22](https://github.com/daredoole/RoomCall/issues/22))
- [x] Compact and kiosk card layouts ([#23](https://github.com/daredoole/RoomCall/issues/23))
- [x] Staggered delivery and retries ([#24](https://github.com/daredoole/RoomCall/issues/24))
- [x] Siri, Apple Watch and CarPlay through Shortcuts ([#14](https://github.com/daredoole/RoomCall/issues/14))

## v0.4 — Apple Home ✅

- [x] Rooms as cameras in the Home app with a talk button ([#15](https://github.com/daredoole/RoomCall/issues/15))

## v0.5 — Live ✅

- [x] Live calls: speakers play while you talk, from the card and Apple Home ([#13](https://github.com/daredoole/RoomCall/issues/13))

## v0.6 — Polish ✅

- [x] Visual card editor, notify entity per room, blueprints, Repairs, diagnostics
- [x] Spanish, German, French and Dutch
- [x] Brand icon shipped with the integration; signed build provenance for integration/Python release artifacts; OpenSSF Scorecard

## v0.7 — Announcements and AI ✅

- [x] Scheduled and triggered announcements to rooms, speakers or people
- [x] Assist / LLM intent and an AI management guide

## v0.8 — Communication workflows

- [x] Explicit current-HA LLM tools and an older-HA API; truthful delivery evidence
- [x] Capability discovery, previews, sound tests and physical-speaker deduplication
- [x] Bounded queued message jobs, expiration, idempotency and explicit acknowledgments
- [x] Questions and transcribed replies through compatible Assist satellites
- [x] Browser room stations with consent-based WebRTC audio calls
- [x] Presence freshness/confidence, language/room preferences and script control adapters
- [x] Optional local voicemail, explicit translation providers and recorded-speech processing
- [x] Local metrics and human/agent journey tests

Details and hardware boundaries: [Messages and room stations](communication.md).

## v0.9 — Guided setup ✅

- [x] Setup tools available before a config entry exists, with a versioned operation catalog
- [x] Reviewable, resumable setup plans with native apply, audio verification and scoped undo
- [x] Announcement management tools with revisions and idempotent creation
- [x] Setup diagnosis and alternate routes that never apply themselves
- [x] One guided setup shared by the card and agents

Details: [Agent setup](agent-setup.md).

## v0.10 — Adaptive card, Siri and iOS ✅

- [x] Card layout follows its width; Talk/Type/Ask everywhere and one keyboard-accessible Settings panel
- [x] Room-specific Siri setup and native Perform Action shortcuts with a live room picker
- [x] Configured chime before typed, Siri, notify and scheduled announcements
- [x] Native iOS client foundation: HA sign-in, Siri announcements and Control Center

Details: [Siri and Shortcuts](siri-shortcuts.md), [Native iOS app](native-ios.md).

## v0.11 — Tasks ✅

- [x] Announcements that stay open until someone confirms the chore was done, with reminders,
      phone actions, a card **To do** list, a sensor, a button and a completion event

Details: [Announcements](announcements.md#tasks-confirm-it-was-actually-done).

## v0.12–0.18 — Native app and message workflows ✅

- [x] Sideloadable iOS app with in-app hold to talk, text, satellite questions, tasks,
      history, foreground doorbell alerts, widgets and Live Activities; no system PTT entitlement
- [x] Configured speech profiles and bounded owner-scoped prepared-speech caching
- [x] Opt-in durable queued text messages with conservative interruption after uncertain dispatch
- [x] Reusable household profiles for destinations, message and voice/chime defaults
- [x] Measured delivery timeline, separate device observations and human acknowledgment
- [x] Per-account RoomCall restrictions and owned-job WebSocket subscriptions

Details and limitations: [Communication](communication.md), [Native iOS app](native-ios.md).

## Further hardware work

- [x] **Hands-free talk-back** with an additional Assist satellite in a Sonos room:
      compatible satellite hardware listens after eligible room-to-room calls and the reply is spoken
      in the caller's room ([#11](https://github.com/daredoole/RoomCall/issues/11))
- [x] **Answer the door from any room or the watch**: rooms ring, phones and cards get the snapshot and an Answer button that opens the camera's live view, with two-way talk where the camera supports it ([#21](https://github.com/daredoole/RoomCall/issues/21))
- [x] Listed in the default HACS store and Home Assistant Marketplace ([hacs/default#12345](https://github.com/hacs/default/pull/12345))
- [ ] Per-room chimes
