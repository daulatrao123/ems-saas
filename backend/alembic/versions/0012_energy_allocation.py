"""Energy allocation policy config (E3): backend-authoritative, delivered to the Pi via energy_config.allocation.
Default disabled. {"enabled": false, "sequence": ["A","B","C","D"], "tolerance_kwh": 1.0, "persistence_s": 300,
 "wings": {"A": {"generation_attribution_enabled": true, "manual_target_kwh": null}, ...}}"""
from alembic import op

revision = "0012_energy_allocation"
down_revision = "0011_energy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS energy_allocation JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE pi_devices DROP COLUMN IF EXISTS energy_allocation")
