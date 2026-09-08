"""LCD messages (website -> Pi display) and Pi-reported storage health.

lcd_messages: one active message per device is enforced by the API (previous active rows are
deactivated on create). The Pi receives the active, unexpired message in the /api/pi/sync reply
and renders it display-only. pi_state.storage_health: JSONB snapshot reported by the Pi.
"""
from alembic import op

revision = "0010_lcd_messages_storage_health"
down_revision = "0009_toggle_telemetry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE IF NOT EXISTS lcd_messages (
            id BIGSERIAL PRIMARY KEY,
            device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
            message VARCHAR(80) NOT NULL,
            active BOOLEAN NOT NULL DEFAULT TRUE,
            created_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            expires_at TIMESTAMPTZ,
            deactivated_at TIMESTAMPTZ,
            delivered_at TIMESTAMPTZ
        )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS lcd_messages_device_active_idx ON lcd_messages (device_id, active, created_at DESC)")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS storage_health JSONB")


def downgrade() -> None:
    op.execute("ALTER TABLE pi_state DROP COLUMN IF EXISTS storage_health")
    op.execute("DROP TABLE IF EXISTS lcd_messages")
