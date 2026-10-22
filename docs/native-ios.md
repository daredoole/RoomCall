# Native iOS prototype

RoomCall includes a SwiftUI iPhone/iPad prototype in `ios/RoomCall`. It broadcasts
microphone audio to HA-controlled speakers using Apple's Push to Talk framework
and RoomCall's existing live WebSocket endpoint. It does not emulate a camera.
The existing Apple Home camera bridge remains an independent, optional route.

## What is implemented

- Home Assistant sign-in through `ASWebAuthenticationSession`, with state checking,
  SHA-256 PKCE, token refresh and device-only Keychain storage. No long-lived token
  is pasted into the app or embedded in a Shortcut.
- Room discovery, explicit destination selection, channel join/leave and native
  system transmission controls. Microphone capture waits for both a transmission
  request and the framework's audio-session activation. Cancelling a pending start,
  stopping, interruptions and deactivation stop capture.
- Live audio resampling to mono 16 kHz signed 16-bit PCM and ordered, bounded
  WebSocket transmission to `roomcall/live`. Finishing explicitly sends the binary
  end marker and reads HA's result. Accepted playback requests remain separate
  from audibility.
- Restoring an authenticated room/channel after app restart. Restoring a channel
  does not start the microphone.
- A Siri/Shortcuts action for typed announcements to a selected room. The action
  returns queue acceptance and never claims the message was heard.
- An `OpenIntent` and Control Center control that open the room interface. Opening
  the app does not join a channel or turn on the microphone.
- Native Dynamic Type, accessibility labels, room controls, and five languages.
  Native strings are generated from the integration's existing translation catalogs.

## Requirements

- RoomCall **0.10.0** installed in Home Assistant. Earlier integrations do not serve
  the native callback metadata endpoint.
- Home Assistant **2026.10 or later**. The app checks the unauthenticated HA
  WebSocket version before sign-in because older supported integration test
  versions do not provide the required PKCE flow.
- iOS/iPadOS **18 or later** is the deployment target. Current API documentation
  was reviewed for iOS 27; simulator CI records the actual SDK/runtime it builds
  and tests. A simulator run is not verification on an iOS 27 phone.
- A Mac with Xcode and XcodeGen to build locally. A physical installation needs
  your own Apple development team and provisioning for Push to Talk, notifications
  and the configured app group. CI uses ad-hoc simulator signing, not a device
  provisioning profile or an App Store distribution identity.

Use a trusted HTTPS HA address. The app permits local networking under Apple's
transport-security rules, but does not bypass certificate validation or globally
disable App Transport Security. Reachability and local HTTP behavior still depend
on the address, device and network configuration.

## Build and run

```bash
brew install xcodegen
python3 scripts/ios_localizations.py --check
xcodegen generate --spec ios/RoomCall/project.yml
open ios/RoomCall/RoomCall.xcodeproj
```

Choose the **RoomCall** scheme. For a physical phone, set your team in Signing &
Capabilities. If you change bundle identifiers, also change the matching app-group
identifier in both entitlements and `RoomCache.defaults` in `Shared/Models.swift`.
Generated Xcode projects, property lists and entitlement files are intentionally
ignored; `project.yml` is their source.

Install, enter your HA address and sign in through HA's own login screen. Choose
a destination and press **Join this room**. **Start talking** begins transmission;
**Stop talking** stops the microphone and finishes the message. System PTT controls
can also control a joined channel. Use **Leave room** before choosing a different
destination. Text announcements are available in Shortcuts under RoomCall.

The HA endpoint `/roomcall/ios` is public IndieAuth callback metadata only. It
advertises `roomcall://auth-callback`; it neither creates credentials nor grants
API access. Normal HA login, PKCE validation, authenticated APIs and caller
permissions still apply.

## Prototype boundaries

This build is **outbound room broadcasting**. Receiving background audio is not
implemented: ephemeral push tokens are not registered with a server, and incoming
PTT push payloads do not claim an active participant. It does not join Apple's
HomePod Intercom system, turn ordinary speakers into Siri-enabled accessories,
or provide Home app Intercom membership.

Background reception needs a provisioned push service, authenticated device-token
registration, expiring message references, download authorization, audio-session
handling and real device tests. Apple's Local Push Connectivity alternative also
requires a separate entitlement approval. These are distinct from ordinary local
outbound streaming and have not been represented as working features.

## Validation

The `iOS / simulator` workflow generates the Xcode project, compiles the app and
Control Center extension, exercises URL validation, PKCE/state, Keychain storage,
native audio conversion, room queries and authenticated API errors, and runs a
large-text sign-in UI test. It uploads the test result bundle, build log and a
simulator app archive when successful. Simulator tests cannot prove real microphone
routing, HomePod behavior, speaker audibility, background wake-up or App Store
eligibility.

The HA integration and standalone server retain their separate **100% Python
statement coverage** gates. Those gates do not measure Swift or JavaScript
execution. Web browser QA independently covers narrow/fixed-height layouts,
200% text, modal focus/keyboard navigation, guided setup and WebRTC cleanup.

## Apple references

- [Creating a Push to Talk app](https://developer.apple.com/documentation/pushtotalk/creating-a-push-to-talk-app/)
- [OpenIntent](https://developer.apple.com/documentation/appintents/openintent)
- [Controls](https://developer.apple.com/documentation/widgetkit/controls-collection)
- [Local push connectivity](https://developer.apple.com/documentation/networkextension/local-push-connectivity)
- [Home Assistant authentication](https://developers.home-assistant.io/docs/auth_api/)
