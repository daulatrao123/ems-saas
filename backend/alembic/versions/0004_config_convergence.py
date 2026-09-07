"""T7: desired vs applied device configuration convergence evidence.

pi_state gains the Pi-reported applied configuration identity and the cloud-derived
convergence state. Existing pi_state.config_version keeps its meaning (desired society
revision at last sync). 0003 is frozen; nothing else is touched.
"""
from alembic import op

revision = "0004_config_convergence"
down_revision = "0003_command_idempotency"
branch_labels = None
depends_on = None

CONFIG_STATES = ("DESIRED", "PENDING_APPLY", "APPLIED", "DRIFTED", "FAILED")


def upgrade() -> None:
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS desired_config_hash TEXT")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS applied_config_version INT")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS applied_config_hash TEXT")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS applied_config_at TIMESTAMPTZ")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS config_state TEXT NOT NULL DEFAULT 'DESIRED'")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS config_error TEXT")
    op.execute(f"""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_pi_state_config_state') THEN
            ALTER TABLE pi_state ADD CONSTRAINT ck_pi_state_config_state
            CHECK (config_state IN ({", ".join(repr(s) for s in CONFIG_STATES)}));
        END IF; END $$;""")


def downgrade() -> None:
    op.execute("ALTER TABLE pi_state DROP CONSTRAINT IF EXISTS ck_pi_state_config_state")
    for col in ("config_error", "config_state", "applied_config_at", "applied_config_hash",
                "applied_config_version", "desired_config_hash"):
        op.execute(f"ALTER TABLE pi_state DROP COLUMN IF EXISTS {col}")
