"""Pi-reported hardware telemetry: physical toggle inputs and GPIO hardware fault.

Additive only. `slot_state.toggle_input` is the Pi-reported physical toggle level
(ON/OFF/UNKNOWN; HIGH = ON, UNKNOWN = GPIO unavailable) and is distinct from
`slot_state.physical_toggle`, which has always carried the CONTACTOR FEEDBACK state.
`pi_state.hardware_fault` carries the controller's GPIO initialisation fault text (NULL = none).
Neither column is configuration: the cloud never writes them except from /api/pi/sync.
"""
from alembic import op

revision = "0009_toggle_telemetry"
down_revision = "0008_society_name_unique"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE slot_state ADD COLUMN IF NOT EXISTS toggle_input TEXT NOT NULL DEFAULT 'UNKNOWN'")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS hardware_fault TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE pi_state DROP COLUMN IF EXISTS hardware_fault")
    op.execute("ALTER TABLE slot_state DROP COLUMN IF EXISTS toggle_input")
