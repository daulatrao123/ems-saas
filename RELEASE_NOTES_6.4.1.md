# EMS SaaS Industrial 6.4.1 — Strict Hardened Candidate

Base: EMS 6.4 targeted hardened ZIP, originally based on main commit `cb15919032596021e732321a4c7c84e60429f5f6`.

## 6.4.1 hardening changes
- Removed duplicate usage/reset fields and duplicate reset application introduced during iterative hardening.
- Added backward-compatible SQLite queue schema migration **before** indexes are created, so legacy queues can boot safely.
- Enforced strict command FSM: normal execution cannot jump from `queued` directly to `executing`; Pi execution follows `delivered → executing → hardware_verified/failed`.
- `UNKNOWN_AFTER_REBOOT` is recovery-only and is excluded from normal queue claim.
- `HARDWARE_VERIFIED` now requires positive verification (`VERIFIED_ON`, `VERIFIED_OFF`, or `GPIO_CONFIRMED`) at the backend boundary.
- Configuration commands received by the Pi now follow `EXECUTING → COMPLETED → ACKED`, matching the backend FSM.
- Invalid/unsupported commands receive a lifecycle-consistent failure path.
- Pi refuses to change hardware if the durable `EXECUTING` state cannot be persisted first; it enters FAULT instead.
- Retired Pi devices cannot be reassigned through society save operations.
- Super-admin dashboard feedback status is capability-clamped to the device's installed feedback hardware.
- Firmware download now authenticates the requesting Pi using both device ID and API key, rather than merely checking that a key exists.
- Added strict role routing: super admin, society admin, and member have separate frontend destinations; member UI is read-only and backend command issuance is restricted to super/society admin.
- Added strict 6.4.1 static gate and runtime compatibility tests.

## Protected subsystems
The following files remain byte-identical to the 6.4 baseline:
- `pi_firmware/gpio_manager.py`
- `pi_firmware/storage_io_manager.py`
- `pi_firmware/storage_manager.py`
- `pi_firmware/logger.py`
- `pi_firmware/memory_manager.py`
- `pi_firmware/resource_guard.py`

## Verification completed
- `python -m py_compile backend/main.py pi_firmware/*.py frontend/test_system.py` — PASS
- `bash -n pi_firmware/setup_pi.sh` — PASS
- `qa/strict_6_4_1_check.py` — 24/24 PASS
- `qa/runtime_compatibility_test.py` — PASS
- Protected subsystem SHA-256 comparison against 6.4 baseline — PASS

## Release status
**STRICT HARDENED CANDIDATE — NOT PRODUCTION CERTIFIED.**

Mandatory external qualification still includes:
1. Raspberry Pi hardware-in-the-loop testing with real contactors and feedback wiring.
2. Power-loss tests during the 500 ms break-before-make interlock and during each command lifecycle stage.
3. 7–30 day storage endurance soak on the exact production USB/storage device and OS image, measuring total system writes, not only application logs.
4. Versioned Alembic database migrations and clean upgrade/rollback qualification.
5. Signed OTA with Ed25519 verification, staged A/B update and rollback protection.
6. Production browser authentication using secure HttpOnly/SameSite cookies rather than localStorage bearer tokens.
7. Dependency, TLS, secrets-management, backup/restore and deployment qualification.
8. Physical verification of the documented GPIO/feedback mapping before field installation.
