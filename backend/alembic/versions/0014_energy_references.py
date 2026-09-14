"""Independent grid reference settings and auditable monthly bill corrections."""
from alembic import op

revision = "0014_energy_references"
down_revision = "0013_energy_calculation_mode"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""ALTER TABLE pi_devices
        ADD COLUMN grid_export_enabled BOOLEAN NOT NULL DEFAULT FALSE,
        ADD COLUMN grid_export_limit_kwh NUMERIC(14,4)
            CHECK (grid_export_limit_kwh >= 0 AND grid_export_limit_kwh <= 10000000),
        ADD COLUMN grid_reference_version INTEGER NOT NULL DEFAULT 0 CHECK (grid_reference_version >= 0),
        ADD CONSTRAINT grid_export_limit_required CHECK (NOT grid_export_enabled OR grid_export_limit_kwh IS NOT NULL)""")
    op.execute("""ALTER TABLE energy_bill_history
        ADD COLUMN source TEXT NOT NULL DEFAULT 'HISTORICAL' CHECK (source = 'HISTORICAL'),
        ADD COLUMN updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL""")


def downgrade():
    op.execute("ALTER TABLE energy_bill_history DROP COLUMN updated_by, DROP COLUMN source")
    op.execute("""ALTER TABLE pi_devices DROP CONSTRAINT grid_export_limit_required,
        DROP COLUMN grid_reference_version, DROP COLUMN grid_export_limit_kwh, DROP COLUMN grid_export_enabled""")