# EMS SaaS Industrial 6.4.1 — Strict Hardened Candidate

Base: `cb15919032596021e732321a4c7c84e60429f5f6` (`main`) plus targeted 6.4 hardening.

## Immediate production-blocker fixes
- Moved `UserLogin` above `/api/auth/login` so FastAPI imports without a `NameError` during Render startup.
- Verified PowerShell bootstrap procedure in release guidance: use `Invoke-RestMethod -Method Post ...` rather than PowerShell's `curl` alias.

## Additional hardening in this candidate
- Preserved strict command FSM; no `queued -> executing` shortcut.
- Preserved 120-second delivery lease and 300-second absolute expiry.
- Legacy STATE_VERSION=4 state documents safely default missing usage/reset fields.
- Legacy SQLite queue schemas are migrated before indexes are created.
- Pi must durably persist `EXECUTING` before contactor hardware is mutated; otherwise the controller enters `FAULT`.
- Super-admin society views now report `feedback_enabled` only when device feedback hardware is installed.
- Retired devices cannot be reassigned through society configuration.
- Firmware download now authenticates both `X-Device-ID` and `X-Api-Key` against the assigned device.
- Existing proven GPIO/storage/resource subsystems remain unchanged.

## Current architecture
- One society can own multiple Pi controllers.
- Each Pi has exactly four physical slots: A/B/C/D.
- Stable hardware identity is `device_id + slot`; display names are not identity.
- Cloud is configuration/coordination authority; Pi is physical-state/runtime authority.
- Cloud commands follow QUEUED -> DELIVERED -> EXECUTING -> HARDWARE_VERIFIED -> COMPLETED -> ACKED, with EXPIRED/FAILED and reboot reconciliation via UNKNOWN_AFTER_REBOOT.

## Offline QA completed
- `py_compile` for backend, Pi firmware, and frontend test script: PASS.
- `bash -n pi_firmware/setup_pi.sh`: PASS.
- Strict source gate: PASS.
- Legacy state compatibility test: PASS.
- Legacy SQLite queue migration test: PASS.

## Not production-certified
HIL contactor/weld/power-loss tests, exact production storage endurance/WAF soak, Alembic migration qualification, signed OTA/A-B rollback, frontend build/CI, dependency vulnerability audit, and final deployment qualification remain mandatory.
