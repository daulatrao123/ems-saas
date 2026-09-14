"""Device energy-data source; independent of Pi-delivered metering/allocation config.

Existing and newly inserted devices explicitly default to AUTO. Manual entries
remain in energy_adjustments; physical telemetry and firmware are unchanged.
"""
from alembic import op

revision = "0013_energy_calculation_mode"
down_revision = "0012_energy_allocation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""ALTER TABLE pi_devices
        ADD COLUMN energy_calculation_mode TEXT NOT NULL DEFAULT 'AUTO'
            CHECK (energy_calculation_mode IN ('AUTO', 'MANUAL')),
        ADD COLUMN energy_calculation_version INTEGER NOT NULL DEFAULT 0
            CHECK (energy_calculation_version >= 0)""")


def downgrade() -> None:
    op.execute("ALTER TABLE pi_devices DROP COLUMN energy_calculation_version, DROP COLUMN energy_calculation_mode")