"""Signed OTA foundation: release signature metadata + per-device OTA state.

firmware_versions gains artifact identity (sha256), Ed25519 signature, signing key id
and min supported firmware. pi_state gains the Pi-reported OTA state (written inside the
existing per-sync upsert -> zero additional writes). 0003/0004/0005 untouched.
"""
from alembic import op

revision = "0006_signed_ota"
down_revision = "0005_device_credentials"
branch_labels = None
depends_on = None

OTA_STATES = ("ACTIVE", "DOWNLOADING", "VERIFIED", "STAGED", "ACTIVATING", "HEALTH_CHECK", "FAILED", "ROLLED_BACK")


def upgrade() -> None:
    for col, typ in (("sha256", "TEXT"), ("signature", "TEXT"), ("key_id", "TEXT"), ("min_firmware_version", "TEXT")):
        op.execute(f"ALTER TABLE firmware_versions ADD COLUMN IF NOT EXISTS {col} {typ}")
    for col, typ in (("ota_desired_version", "TEXT"), ("ota_state", "TEXT"), ("ota_version", "TEXT"),
                     ("ota_artifact_sha256", "TEXT"), ("ota_attempts", "INT"), ("ota_last_error", "TEXT"),
                     ("ota_updated_at", "TIMESTAMPTZ"), ("last_good_firmware_version", "TEXT")):
        op.execute(f"ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS {col} {typ}")
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_pi_state_ota_state') THEN
            ALTER TABLE pi_state ADD CONSTRAINT ck_pi_state_ota_state
            CHECK (ota_state IS NULL OR ota_state IN ({", ".join(repr(s) for s in OTA_STATES)}));
        END IF; END $$;""")


def downgrade() -> None:
    op.execute("ALTER TABLE pi_state DROP CONSTRAINT IF EXISTS ck_pi_state_ota_state")
    for col in ("last_good_firmware_version", "ota_updated_at", "ota_last_error", "ota_attempts",
                "ota_artifact_sha256", "ota_version", "ota_state", "ota_desired_version"):
        op.execute(f"ALTER TABLE pi_state DROP COLUMN IF EXISTS {col}")
    for col in ("min_firmware_version", "key_id", "signature", "sha256"):
        op.execute(f"ALTER TABLE firmware_versions DROP COLUMN IF EXISTS {col}")
