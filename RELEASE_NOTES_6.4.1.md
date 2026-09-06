# EMS SaaS 6.4.1 — Strict Hardened Candidate

Base: `daulatrao123/ems-saas` main at `cb15919032596021e732321a4c7c84e60429f5f6`

## Deployment blocker fixed

The Render startup failure caused by parameterizing PostgreSQL DDL is eliminated by moving schema creation/migration out of `backend/main.py` entirely. The reset-day default is now applied by the Alembic migration using a PostgreSQL-safe literal.

Render now runs:

`alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port $PORT`

## 6.4.1 hardening included

- Alembic versioned migration baseline for the current relational schema and legacy wing/slot normalization.
- No application-startup DDL in `backend/main.py`.
- Strict command FSM: `QUEUED -> DELIVERED -> EXECUTING -> HARDWARE_VERIFIED -> COMPLETED -> ACKED`, with explicit failure/expiry/recovery branches.
- 120-second delivery lease and 300-second absolute command expiry.
- `HARDWARE_VERIFIED` requires explicit verification state.
- `UNKNOWN_AFTER_REBOOT` is not eligible for normal queue execution.
- Legacy SQLite command databases are extended before indexes are created.
- Legacy `STATE_VERSION=4` JSON safely defaults missing usage counters.
- Hardware mutation is blocked if the Pi cannot persist its `EXECUTING` state first; the Pi enters `FAULT` instead.
- Retired Pi devices cannot be silently reassigned through society configuration.
- Dashboard feedback capability is clamped to actual device feedback hardware.
- Explicit, non-formatting USB data-device provisioning remains enforced.
- Pi controller service runs as `pi:pi` with GPIO/I2C supplementary groups.
- Pi API credentials are sent through headers by the current client, not duplicated in JSON.
- Existing GPIO/storage/resource subsystems remain byte-identical to the main baseline.

## QA executed offline

- Python syntax/compile checks: PASS.
- Shell syntax check for `setup_pi.sh`: PASS.
- Strict source-level release gate: PASS.
- Legacy SQLite queue migration/runtime compatibility: PASS.
- Legacy `STATE_VERSION=4` state compatibility: PASS.
- Alembic offline SQL generation: PASS; PostgreSQL migration context reports transactional DDL.

## Remaining qualification gates — NOT certified by this ZIP

These require target infrastructure/hardware and must remain release blockers for a true industrial production certification:

1. Physical HIL: welded contactor, missing feedback, feedback mismatch, and power-loss during the 500 ms interlock.
2. 7–30 day storage endurance soak on the exact production Raspberry Pi OS + USB device; measure actual system WAF and total physical writes.
3. Live PostgreSQL migration rehearsal against a backup/staging clone of the production schema.
4. HttpOnly/Secure/SameSite cookie authentication and refresh-token rotation.
5. Ed25519-signed firmware verification plus target-specific A/B boot and automatic rollback.

**Release status: HARDENED CANDIDATE — NOT INDUSTRIAL-PRODUCTION CERTIFIED.**
