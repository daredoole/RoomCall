# Troubleshooting

The HA integration and standalone server use different settings; follow the
matching section below. Native sign-in has a separate [error guide](native-ios.md#sign-in-errors).

## "Microphone blocked"

Browsers only expose the microphone on HTTPS pages or `localhost`. Serve RoomCall through a
reverse proxy with a certificate your devices trust, then reload and allow the mic.

## No speakers found { #vlans }

For the HA integration, add the speaker's native integration in Home Assistant,
then select its media player under RoomCall → Configure. RoomCall uses HA's entities.

For the standalone server, discovery uses mDNS:

mDNS does not cross VLANs or subnets on its own.

- Run RoomCall on the same network as the speakers, **or**
- enable an mDNS reflector/repeater on your router (UniFi: *Multicast DNS*), **or**
- list the speakers in `speakers:` in [the config](configuration.md).

## Speakers report success but stay silent

An accepted request does not establish audible playback. Check the selected room,
volume/mute, device state and active queue. With HA, inspect independent fetch and
matching playback evidence in history; unsupported evidence remains unknown.

For HA-hosted audio, verify HA's **internal URL** is reachable from the speaker's
network. For the standalone server, check `public_url` (printed at startup).
Firewalls, private certificates the speaker cannot trust, and expired media URLs
can prevent downloads. External audio and direct TTS have their own provider URLs.

## Everywhere is slower, or the native app reports a connection error

Everywhere includes all selected routes, including devices that may need to wake
or launch a player. Repeated calls can queue behind existing playback/restoration;
a fast single-room call does not establish the other routes' latency.

Update the iPhone app to **0.18.2 or later**. Older builds awaited transcription
and history after playback acceptance and could time out. Current builds complete
on the independent acceptance receipt. Lost confirmation is an unknown outcome:
check history before repeating the message. This correction does not remove
hardware startup, network or queue delays.

## Sonos does not resume my music

RoomCall restores the previous queue and volume after the clip. Line-in, TV audio and some
streaming-service sessions cannot be resumed by any third-party controller.

## AirPlay is slow

AirPlay can buffer audio, and actual startup depends on the device and route.
Test an available native alternative for the same physical speaker. The standalone
discovery prefers native Sonos control; HA setup recommends routes and deduplicates
only when it has physical identity evidence or an explicit alias.
