# Roadmap

## v0.1 — Push-to-talk ✅

- [x] mDNS discovery of Sonos, AirPlay and Cast speakers (standalone server)
- [x] Rooms, groups and "everywhere"
- [x] Browser hold-to-talk with level meter and delivery report
- [x] HTTP API, CLI, demo mode, token auth
- [x] Native Home Assistant integration: config flow, rooms from areas, `roomcall.broadcast`
- [x] Push-to-talk dashboard card, announce mode, walkie-talkie chirp, silent-mic detection

## v0.2 — Talk back

Sonos, HomePod and Nest microphones are reserved for their own assistants, so replies come from
devices RoomCall can listen to:

- [ ] **Reply from a phone or tablet** — every call sends a notification with a *Reply* button
      that opens the card already pointed at the caller's room
- [ ] **Voice satellites as room stations** — Home Assistant Voice / ESPHome devices chirp after a
      call and record a short reply for the caller
- [ ] Doorbells with two-way audio (via go2rtc)

## v0.3 — HomeKit

- [ ] Expose each room as a HomeKit camera with two-way audio, so the Home app's talk button
      broadcasts to that room

## Later

- [ ] Streaming while you talk (lower perceived delay)
- [ ] Container image and Home Assistant add-on for the standalone server
