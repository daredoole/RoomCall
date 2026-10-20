# Troubleshooting

## "Microphone blocked"

Browsers only expose the microphone on HTTPS pages or `localhost`. Serve RoomCall through a
reverse proxy with a certificate your devices trust, then reload and allow the mic.

## No speakers found { #vlans }

mDNS does not cross VLANs or subnets on its own.

- Run RoomCall on the same network as the speakers, **or**
- enable an mDNS reflector/repeater on your router (UniFi: *Multicast DNS*), **or**
- list the speakers in `speakers:` in [the config](configuration.md).

## Speakers report success but stay silent

The speaker could not download the clip. Check that `public_url` (printed at startup) is reachable
**from the speaker's network** — firewalls between VLANs commonly block it.

## Sonos does not resume my music

RoomCall restores the previous queue and volume after the clip. Line-in, TV audio and some
streaming-service sessions cannot be resumed by any third-party controller.

## AirPlay is slow

AirPlay always adds about two seconds of buffering. Prefer native Sonos or Cast control for the
same device when available — RoomCall does this automatically.
