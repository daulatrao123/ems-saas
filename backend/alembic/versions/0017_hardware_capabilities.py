"""Device hardware capabilities. Separate from the GPIO profile name and from allocation mode.

Existing feedback_hardware_installed is copied, not guessed. A meter is installed
only when energy_meters.enabled is true. A serial by itself is not installation.
Serial values are left untouched. LCD stays unspecified (installed null).
"""
from alembic import op

revision = "0017_hardware_capabilities"
down_revision = "0016_allocation_mode"
branch_labels = None
depends_on = None

_DEFAULT = """'{
  "capability_version": 1,
  "contactor_feedback": {"installed": false, "channels": {"A": {"enabled": false}, "B": {"enabled": false}, "C": {"enabled": false}, "D": {"enabled": false}}},
  "generation_meter": {"installed": false, "enabled": false},
  "consumption_meters": {"A": {"installed": false, "enabled": false}, "B": {"installed": false, "enabled": false}, "C": {"installed": false, "enabled": false}, "D": {"installed": false, "enabled": false}},
  "lcd": {"installed": null, "enabled": false}
}'::jsonb"""


def upgrade():
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS hardware_capabilities JSONB")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS reported_hardware JSONB")
    op.execute("""
        UPDATE pi_devices d
        SET hardware_capabilities = jsonb_build_object(
            'capability_version', 1,
            'contactor_feedback', jsonb_build_object(
                'installed', COALESCE(d.feedback_hardware_installed, false),
                'channels', jsonb_build_object(
                    'A', jsonb_build_object('enabled', COALESCE(d.feedback_hardware_installed, false) AND COALESCE((SELECT feedback_enabled FROM slot_configs sc WHERE sc.device_id = d.id AND sc.slot = 'A'), false)),
                    'B', jsonb_build_object('enabled', COALESCE(d.feedback_hardware_installed, false) AND COALESCE((SELECT feedback_enabled FROM slot_configs sc WHERE sc.device_id = d.id AND sc.slot = 'B'), false)),
                    'C', jsonb_build_object('enabled', COALESCE(d.feedback_hardware_installed, false) AND COALESCE((SELECT feedback_enabled FROM slot_configs sc WHERE sc.device_id = d.id AND sc.slot = 'C'), false)),
                    'D', jsonb_build_object('enabled', COALESCE(d.feedback_hardware_installed, false) AND COALESCE((SELECT feedback_enabled FROM slot_configs sc WHERE sc.device_id = d.id AND sc.slot = 'D'), false))
                )
            ),
            'generation_meter', COALESCE((
                SELECT CASE
                    WHEN enabled IS TRUE THEN jsonb_build_object('installed', true, 'enabled', true)
                    ELSE jsonb_build_object('installed', false, 'enabled', false)
                END FROM energy_meters m WHERE m.device_id = d.id AND m.meter_id = 'M1'
            ), jsonb_build_object('installed', false, 'enabled', false)),
            'consumption_meters', jsonb_build_object(
                'A', COALESCE((SELECT CASE WHEN enabled IS TRUE THEN jsonb_build_object('installed', true, 'enabled', true) ELSE jsonb_build_object('installed', false, 'enabled', false) END FROM energy_meters m WHERE m.device_id = d.id AND m.meter_id = 'M2'), jsonb_build_object('installed', false, 'enabled', false)),
                'B', COALESCE((SELECT CASE WHEN enabled IS TRUE THEN jsonb_build_object('installed', true, 'enabled', true) ELSE jsonb_build_object('installed', false, 'enabled', false) END FROM energy_meters m WHERE m.device_id = d.id AND m.meter_id = 'M3'), jsonb_build_object('installed', false, 'enabled', false)),
                'C', COALESCE((SELECT CASE WHEN enabled IS TRUE THEN jsonb_build_object('installed', true, 'enabled', true) ELSE jsonb_build_object('installed', false, 'enabled', false) END FROM energy_meters m WHERE m.device_id = d.id AND m.meter_id = 'M4'), jsonb_build_object('installed', false, 'enabled', false)),
                'D', COALESCE((SELECT CASE WHEN enabled IS TRUE THEN jsonb_build_object('installed', true, 'enabled', true) ELSE jsonb_build_object('installed', false, 'enabled', false) END FROM energy_meters m WHERE m.device_id = d.id AND m.meter_id = 'M5'), jsonb_build_object('installed', false, 'enabled', false))
            ),
            'lcd', jsonb_build_object('installed', NULL, 'enabled', false)
        )
        WHERE d.hardware_capabilities IS NULL
    """)
    op.execute(f"ALTER TABLE pi_devices ALTER COLUMN hardware_capabilities SET DEFAULT {_DEFAULT}")


def downgrade():
    op.execute("ALTER TABLE pi_state DROP COLUMN IF EXISTS reported_hardware")
    op.execute("ALTER TABLE pi_devices DROP COLUMN IF EXISTS hardware_capabilities")
