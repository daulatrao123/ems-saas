"""Per-device firmware schedule and society timezone.

Scheduling is stored on the existing ota operation. Activation remains a Pi
decision, and only one open operation is allowed per device.
"""
from alembic import op

revision = "0019_firmware_schedule"
down_revision = "0018_firmware_releases"
branch_labels = None
depends_on = None

_OPEN = "'REQUESTED','SCHEDULED','WAITING','DEFERRED','DOWNLOADING','VERIFYING','STAGED','INSTALLING','RESTARTING','HEALTH_CHECK','ROLLING_BACK'"


def upgrade() -> None:
    op.execute("ALTER TABLE societies ADD COLUMN IF NOT EXISTS timezone TEXT NOT NULL DEFAULT 'Asia/Kolkata'")
    op.execute("ALTER TABLE ota_operations ADD COLUMN IF NOT EXISTS scheduled_for TIMESTAMPTZ")
    op.execute("ALTER TABLE ota_operations ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ")
    op.execute("ALTER TABLE ota_operations ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE ota_operations ADD COLUMN IF NOT EXISTS previous_version TEXT")
    op.execute("DROP INDEX IF EXISTS ux_ota_one_open_operation")
    op.execute(f"""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ota_one_open_operation
        ON ota_operations (device_id)
        WHERE state IN ({_OPEN})""")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_ota_one_open_operation")
    op.execute("""
        CREATE UNIQUE INDEX IF NOT EXISTS ux_ota_one_open_operation
        ON ota_operations (device_id)
        WHERE state IN ('REQUESTED','DOWNLOADING','VERIFYING','STAGED','INSTALLING','RESTARTING','HEALTH_CHECK','ROLLING_BACK')""")
    op.execute("ALTER TABLE ota_operations DROP COLUMN IF EXISTS previous_version")
    op.execute("ALTER TABLE ota_operations DROP COLUMN IF EXISTS completed_at")
    op.execute("ALTER TABLE ota_operations DROP COLUMN IF EXISTS started_at")
    op.execute("ALTER TABLE ota_operations DROP COLUMN IF EXISTS scheduled_for")
    op.execute("ALTER TABLE societies DROP COLUMN IF EXISTS timezone")
