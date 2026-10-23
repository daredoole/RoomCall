# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's
[private vulnerability reporting](https://github.com/daredoole/RoomCall/security/advisories/new)
instead. You will get a reply within a week, and a fix or mitigation plan as soon as the issue is
confirmed.

## Verifying a release

The integration ZIP and Python wheel/sdist produced by the release workflow have
signed build provenance (SLSA v1):

```bash
gh attestation verify roomcall.zip --repo daredoole/RoomCall
```

The separately attached **unsigned iOS IPA** and its build/checksum JSON are not
covered by that provenance bundle. The JSON identifies the source workflow and
SHA-256 digest; a checksum alone is not a signed provenance attestation.

## Design notes

- Push-to-talk uploads (`/api/roomcall/talk`) and the room list require a logged-in Home Assistant
  user.
- HA clip downloads (`/api/roomcall/clip/…`) are unauthenticated because speakers
  cannot log in. Clip IDs contain 128 bits of randomness and stop being served
  after two minutes, or sooner through bounded-store eviction/reload. Live audio
  URLs and the static chime endpoint are also unauthenticated; treat media URLs
  as sensitive. The endpoint observation token is not an authentication check.
- Call history is held in memory (at most 20 records); available recording audio
  has a 24-hour TTL. History/replay endpoints require authentication. Metadata
  remains until eviction/reload; optional voicemail and durable message jobs
  explicitly retain content in HA storage and potentially HA backups. See the
  [retention contract](docs/communication.md).
- Speech-to-text, TTS, translation, mobile notifications and optional shortcut
  signing follow the configured providers and may use external services. Native
  sign-in also fetches public OAuth metadata; HA credentials are not sent there.
- The standalone server supports a shared token (`token:` in `roomcall.yaml`); put it behind HTTPS
  if it is reachable from untrusted networks. Its clip URLs use 96 bits of randomness,
  expire after two minutes and are not protected by the API token.
