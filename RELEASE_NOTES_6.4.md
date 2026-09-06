# EMS SaaS Industrial 6.4.1 — Strict Hardened Candidate

Baseline: `cb15919032596021e732321a4c7c84e60429f5f6` (`main`)

This ZIP is the corrected targeted hardening candidate. It resolves the previously identified source-level lifecycle, compatibility, migration-order, feedback-consistency, retirement, and delivery-lease issues.

### Included hardening
- Strict command FSM with no QUEUED → EXECUTING shortcut.
- 120-second delivery lease and 300-second absolute expiry.
- HARDWARE_VERIFIED requires actual verification evidence.
- EXECUTING state is durably persisted before hardware actuation.
- UNKNOWN_AFTER_REBOOT never enters normal execution; reconciliation is mandatory.
- Explicit terminal ACK then ACKED lifecycle.
- Legacy STATE_VERSION=4 JSON compatibility.
- Legacy SQLite queue schema migration occurs before indexes.
- Monthly usage/reset accounting persisted.
- Feedback configuration is constrained by real device capability in admin and Pi sync views.
- Retired devices cannot be silently re-assigned.
- Pi credentials use request headers rather than JSON credentials.
- Explicit EMS storage device, existing ext4 only, no formatting.
- Least-privilege systemd controller service.

### Validation completed in this build
- Python compilation: PASS.
- Bash syntax: PASS.
- Strict self-contained source gate: PASS.
- Legacy queue migration runtime test: PASS when run against a temporary legacy DB.
- Legacy STATE_VERSION=4 load compatibility: PASS.

### Not production-certified by source review alone
Hardware HIL/electrical verification, storage endurance/NAND wear qualification, Alembic production migrations, signed OTA/rollback, production frontend auth/CI, dependency security, and final Raspberry Pi/systemd deployment validation remain mandatory gates.
