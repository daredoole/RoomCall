# Agent setup implementation

Target: an administrator can start RoomCall's HA configuration flow, discover
requirements, review/apply a configuration, resume after interruption, test,
verify, and undo through the same API used by the dashboard.

- [x] Bootstrap tools before a config entry exists; machine-readable catalog.
- [x] Persisted, owner-scoped setup plans; schema validation and stale-state checks.
- [x] Apply with native HA config/options flows; scoped undo and verified readiness.
- [x] Dedicated announcement list/create/update/enable/disable/delete tools.
- [x] Actionable errors and diagnosis, with explicitly planned alternate routes.
- [x] Shared human setup controls, review, tests, resume, verification and undo.
- [x] Agent acceptance journeys: fresh setup, interruption, conflict, failed route,
  announcements, unauthorized access and recovery without documentation lookup.
- [x] Complete translations, documentation, synchronized version/changelog.
- [x] 100% Python statement coverage on both supported HA versions and standalone;
  browser regressions, lint/types/docs/package checks and required PR CI gates.

Setup configuration and announcement administration require an authenticated HA
administrator. Plans carry revisions, expire, and cannot overwrite intervening
changes. Human audibility confirmation remains separate from device evidence.
Optional retention/cloud providers and permission/exposure changes are never
enabled as automatic repairs.

Local validation: current HA 225 passed; older supported HA 224 passed and one
expected compatibility skip, both at 100% statement coverage. Standalone: 33
passed, 100%. Browser layout/setup/WebRTC QA, Ruff, mypy, strict docs build,
package build and 21st UI review pass. CI records the final PR validation.
