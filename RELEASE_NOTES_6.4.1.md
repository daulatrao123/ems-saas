# EMS SaaS Industrial 6.4.1 — Strict Hardened Candidate

Base: GitHub `daulatrao123/ems-saas`, main commit `cb15919032596021e732321a4c7c84e60429f5f6`.

## Security / reliability changes

- Database schema DDL removed from FastAPI startup. Production schema is now controlled by Alembic.
- Render startup runs `alembic upgrade head` before Uvicorn.
- Added idempotent industrial baseline migration that adopts existing v6.x schemas and normalizes legacy wing/slot names.
- Browser authentication moved from `localStorage` JWT storage to `HttpOnly; Secure; SameSite=None` cookie sessions with `/api/auth/me` and `/api/auth/logout`.
- Added controlled bootstrap administrator password recovery using `EMS_BOOTSTRAP_RECOVERY_TOKEN` + current `EMS_BOOTSTRAP_PASSWORD`.
- Strict command FSM: queued commands cannot jump directly to executing; `HARDWARE_VERIFIED` requires explicit verification state.
- Retired devices cannot be reassigned through society configuration.
- Retired societies cannot receive new commands.
- Super-admin and society-admin frontend routes now verify server-side session role rather than trusting browser storage.
- Added Ed25519 firmware signing and SHA-256 integrity metadata on firmware records.
- Pi firmware download requires authenticated device headers and returns a signed manifest rather than unsigned source text.
- Added Pi-side Ed25519 verification and application A/B staging with atomic active/pending markers and automatic rollback of an unconfirmed boot.
- EMS systemd service remains least-privilege (`User=pi`, `Group=pi`, `NoNewPrivileges=true`) and uses the OTA boot selector.
- Pi refuses to mutate hardware if it cannot durably persist the `EXECUTING` state first.

## QA evidence included

- `qa/strict_6_4_1_check.py` — static industrial invariants.
- `qa/runtime_6_4_1_test.py` — signed OTA verification and legacy state compatibility.
- `qa/HIL_TEST_PLAN.md` — required physical contactor/feedback/power-loss qualification.
- `qa/STORAGE_ENDURANCE_PLAN.md` — required 7–30 day production-storage soak and WAF measurement.

## Important release status

This is a **strict hardened candidate**, not a claim of field certification. The following require the actual production environment:

1. Physical HIL qualification with the welded-contactor, missing-feedback and 500 ms power-loss cases.
2. 7–30 day endurance soak on the exact production Pi OS + USB device to measure system-level write amplification.
3. Real production deployment of Alembic against a backup/restore-tested database.
4. OTA qualification on the exact Raspberry Pi boot/storage layout, including boot-failure rollback.
5. Frontend HTTPS deployment and browser cookie/CORS verification.

Do not push to production solely because the static checks pass.
