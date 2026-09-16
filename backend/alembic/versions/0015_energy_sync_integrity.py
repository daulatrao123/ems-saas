"""Bounded clock quarantine and versioned energy-target delivery evidence.

Revision ID: 0015_energy_sync_integrity
Revises: 0014_energy_references
"""
from alembic import op

revision = "0015_energy_sync_integrity"
down_revision = "0014_energy_references"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""CREATE TABLE energy_sync_state (
        device_id UUID PRIMARY KEY REFERENCES pi_devices(id) ON DELETE CASCADE,
        target_signature JSONB,
        reported_config_version INTEGER,
        reported_at TIMESTAMPTZ,
        clock_report JSONB
    )""")
    op.execute("""CREATE TABLE energy_day_quarantine (
        device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
        date_key VARCHAR(40) NOT NULL,
        reason VARCHAR(80) NOT NULL,
        payload_preview TEXT NOT NULL CHECK (octet_length(payload_preview) <= 65536),
        first_seen TIMESTAMPTZ NOT NULL,
        last_seen TIMESTAMPTZ NOT NULL,
        PRIMARY KEY (device_id, date_key)
    )""")
    op.execute("CREATE INDEX energy_quarantine_recent_idx ON energy_day_quarantine(device_id, last_seen DESC)")


def downgrade():
    op.execute("DROP TABLE energy_day_quarantine")
    op.execute("DROP TABLE energy_sync_state")