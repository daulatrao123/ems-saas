# Secure OTA Gate — Gate 4b

The current candidate does **not** claim secure OTA completion.

Before enabling OTA in production, implement and qualify:

1. Ed25519 signing key held outside application source/database.
2. Firmware artifact digest + signature stored with immutable version metadata.
3. Pi ships with a pinned public verification key or secure trust anchor.
4. Pi verifies version, digest, signature, and artifact format before staging.
5. Atomic A/B slots or equivalent rollback mechanism.
6. Automatic rollback when health checks fail after reboot.
7. Anti-rollback policy for production versions.
8. Key rotation/revocation procedure.
9. Audit trail for signer, version, target device, result, and rollback.

Never enable a forced OTA path until these controls have passed physical and fault-injection tests.
