"""Multi-file firmware releases and one open OTA operation per device.

Existing firmware_versions rows and the single-file download stay in place.
pi_state.ota_state values are unchanged. ota_agent is reported only by firmware
that can stage a multi-file release.
"""
from alembic import op

revision = "0018_firmware_releases"
down_revision = "0017_hardware_capabilities"
branch_labels = None
depends_on = None

_OPEN = "'REQUESTED','DOWNLOADING','VERIFYING','STAGED','INSTALLING','RESTARTING','HEALTH_CHECK','ROLLING_BACK'"


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS firmware_releases (
            version TEXT PRIMARY KEY,
            hardware_profile TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'DRAFT',
            manifest JSONB NOT NULL,
            package_sha256 TEXT NOT NULL,
            signature TEXT,
            key_id TEXT,
            notes TEXT,
            created_by TEXT,
            approved_by TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            approved_at TIMESTAMPTZ,
            CONSTRAINT ck_firmware_release_status CHECK (status IN ('DRAFT', 'APPROVED', 'REVOKED'))
        )""")
    op.execute("""
        CREATE TABLE IF NOT EXISTS ota_operations (
            id UUID PRIMARY KEY,
            device_id UUID NOT NULL REFERENCES pi_devices(id),
            firmware_version TEXT NOT NULL,
            state TEXT NOT NULL,
            created_by TEXT,
            created_at TIMESTAMPTZ NOT NULL,
            updated_at TIMESTAMPTZ NOT NULL,
            error TEXT
        )""")
    op.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ota_one_open_operation
        ON ota_operations (device_id)
        WHERE state IN ({_OPEN})""")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS ota_agent TEXT")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_ota_one_open_operation")
    op.execute("DROP TABLE IF EXISTS ota_operations")
    op.execute("DROP TABLE IF EXISTS firmware_releases")
    op.execute("ALTER TABLE pi_state DROP COLUMN IF EXISTS ota_agent")
