# EMS SaaS Industrial 6.4.1 — Strict Targeted Hardening

Base: `cb15919032596021e732321a4c7c84e60429f5f6` (`main`)

## Scope

This release is intentionally built from the stable main baseline. The proven GPIO, storage I/O, storage manager, logger, memory manager, and resource guard implementations are preserved unchanged.

### Targeted changes
- Fixed PostgreSQL/psycopg startup DDL bug: `ALTER TABLE ... SET DEFAULT` now uses a validated literal instead of a bound parameter.
- Added conservative pre-hardware persistence gate: the Pi will not mutate GPIO unless the EXECUTING state is durably saved.
- Hardened retired-device lifecycle so retired devices cannot be silently reassigned.
- Kept feedback capability consistent in Super-Admin dashboard data.
- Strict queue FSM disallows direct `QUEUED -> EXECUTING`.
- Durable monthly usage/reset-period state while retaining state format version 4 compatibility.
- Explicit `HARDWARE_VERIFIED` and `UNKNOWN_AFTER_REBOOT` command lifecycle handling.
- Recovery of interrupted `EXECUTING`, `HARDWARE_VERIFIED`, and `UNKNOWN_AFTER_REBOOT` local queue entries against reconciled hardware truth.
- Explicit terminal command ACK followed by `ACKED` confirmation.
- Pi authentication uses `X-Device-ID` and `X-Api-Key` headers; body-key fallback remains for legacy firmware compatibility.
- Backend command transition validation, 120-second delivery lease, and 300-second absolute command expiry.
- Device and society retirement instead of destructive deletion.
- Society reset-day persistence and validation.
- Feedback-enabled configuration is constrained by device feedback capability in Pi sync.
- Explicit EMS storage device provisioning; no automatic formatting.
- Least-privilege systemd unit (`User=pi`, GPIO/I2C supplementary groups, restricted filesystem/device access).
- Hardcoded integration-test credentials replaced by environment variables.
- Empty obsolete `pi_firmware/storage_health.py` removed.

## Files intentionally protected
- `pi_firmware/gpio_manager.py`
- `pi_firmware/storage_io_manager.py`
- `pi_firmware/storage_manager.py`
- `pi_firmware/logger.py`
- `pi_firmware/memory_manager.py`
- `pi_firmware/resource_guard.py`

## Offline QA completed
- Python compilation of backend, Pi firmware, and frontend test script: PASS.
- `bash -n pi_firmware/setup_pi.sh`: PASS.
- Git whitespace check: PASS.
- OfflineQueue lifecycle runtime test: PASS.
- OfflineQueue reboot-recovery state test: PASS.
- Pi state persistence/legacy-compatible state test: PASS.
- Strict targeted source gate: PASS.

## Not certified by this ZIP
Hardware HIL, GPIO electrical polarity, contactor feedback wiring, power-fail testing, storage endurance/NAND wear, OTA signature/rollback, dependency vulnerability remediation, production database migration qualification, and full frontend CI build remain mandatory before production certification.
