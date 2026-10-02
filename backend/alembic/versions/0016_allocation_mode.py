"""Persisted allocation mode, separate from energy calculation mode.

allocation_mode is AUTO, MANUAL, or DAY_BASED. Existing devices default to AUTO.
day_allocation stores the desired day/unit configuration. allocation_batch_id
groups the existing per-wing set_days commands created by one apply.
"""
from alembic import op

revision = "0016_allocation_mode"
down_revision = "0015_energy_sync_integrity"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE pi_devices
        ADD COLUMN IF NOT EXISTS allocation_mode TEXT NOT NULL DEFAULT 'AUTO'
            CHECK (allocation_mode IN ('AUTO', 'MANUAL', 'DAY_BASED'))""")
    op.execute("""ALTER TABLE pi_devices
        ADD COLUMN IF NOT EXISTS allocation_mode_version INTEGER NOT NULL DEFAULT 0
            CHECK (allocation_mode_version >= 0)""")
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS day_allocation JSONB")
    op.execute("ALTER TABLE pi_commands ADD COLUMN IF NOT EXISTS allocation_batch_id UUID")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pi_commands_allocation_batch ON pi_commands(allocation_batch_id)")


def downgrade():
    op.execute("DROP INDEX IF EXISTS idx_pi_commands_allocation_batch")
    op.execute("ALTER TABLE pi_commands DROP COLUMN IF EXISTS allocation_batch_id")
    op.execute("ALTER TABLE pi_devices DROP COLUMN IF EXISTS day_allocation")
    op.execute("ALTER TABLE pi_devices DROP COLUMN IF EXISTS allocation_mode_version")
    op.execute("ALTER TABLE pi_devices DROP COLUMN IF EXISTS allocation_mode")
