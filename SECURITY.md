# Security policy

## Supported versions

Only the latest release receives fixes.

## Reporting a vulnerability

Please **do not open a public issue**. Use GitHub's
[private vulnerability reporting](https://github.com/daredoole/RoomCall/security/advisories/new)
instead. You will get a reply within a week, and a fix or mitigation plan as soon as the issue is
confirmed.

## Verifying a release

Every release file has signed build provenance (SLSA v1) created by this repository's release
workflow:

```bash
gh attestation verify roomcall.zip --repo daredoole/RoomCall
```

## Design notes

- Push-to-talk uploads (`/api/roomcall/talk`) and the room list require a logged-in Home Assistant
  user.
- Clip downloads (`/api/roomcall/clip/…`) are unauthenticated because speakers cannot log in. Each
  clip uses a random 128-bit id and is deleted after two minutes.
- The standalone server supports a shared token (`token:` in `roomcall.yaml`); put it behind HTTPS
  if it is reachable from untrusted networks.
