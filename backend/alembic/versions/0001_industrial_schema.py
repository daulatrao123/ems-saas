"""Create EMS industrial schema and normalize legacy wing-era schema."""
from alembic import op
import sqlalchemy as sa

revision = "0001_industrial_schema"
down_revision = None
branch_labels = None
depends_on = None

SLOTS = ("A", "B", "C", "D")


def upgrade():
    # Legacy normalization is intentionally versioned here, never at app startup.
    op.execute("""
    DO $$ BEGIN
      IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='wing_configs') AND
         NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='slot_configs') THEN
        ALTER TABLE wing_configs RENAME TO slot_configs;
      END IF;
      IF EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='wing_state') AND
         NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='slot_state') THEN
        ALTER TABLE wing_state RENAME TO slot_state;
      END IF;
      IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='slot_configs' AND column_name='wing_code') THEN
        ALTER TABLE slot_configs RENAME COLUMN wing_code TO slot;
      ELSIF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='slot_configs' AND column_name='slot_code') THEN
        ALTER TABLE slot_configs RENAME COLUMN slot_code TO slot;
      END IF;
      IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='slot_state' AND column_name='wing_code') THEN
        ALTER TABLE slot_state RENAME COLUMN wing_code TO slot;
      ELSIF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='slot_state' AND column_name='slot_code') THEN
        ALTER TABLE slot_state RENAME COLUMN slot_code TO slot;
      END IF;
      IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pi_commands' AND column_name='wing') THEN
        ALTER TABLE pi_commands RENAME COLUMN wing TO slot;
      END IF;
      IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pi_state' AND column_name='active_wing') THEN
        ALTER TABLE pi_state RENAME COLUMN active_wing TO active_slot;
      END IF;
    END $$;
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS societies (
      id SERIAL PRIMARY KEY, name TEXT, location TEXT, plan TEXT, status TEXT,
      tailscale_ip TEXT, pi_port INT, society_code TEXT,
      config_version INT NOT NULL DEFAULT 1, reset_day INT NOT NULL DEFAULT 15
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS users (
      id SERIAL PRIMARY KEY, email TEXT UNIQUE NOT NULL, name TEXT, password TEXT NOT NULL,
      role TEXT NOT NULL, society_id INT REFERENCES societies(id) ON DELETE SET NULL
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS pi_devices (
      id UUID PRIMARY KEY, society_id INT REFERENCES societies(id) ON DELETE SET NULL,
      name TEXT, api_key_hash TEXT UNIQUE, firmware_version TEXT, last_seen TIMESTAMPTZ,
      status TEXT NOT NULL DEFAULT 'INVENTORY', hardware_profile TEXT NOT NULL DEFAULT 'EMS-4CH-v1',
      feedback_hardware_installed BOOLEAN NOT NULL DEFAULT FALSE
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS slot_configs (
      device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
      slot TEXT NOT NULL, display_name TEXT, target_days INT NOT NULL DEFAULT 0,
      disabled BOOLEAN NOT NULL DEFAULT FALSE, feedback_enabled BOOLEAN NOT NULL DEFAULT FALSE,
      PRIMARY KEY(device_id, slot)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS slot_state (
      device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
      slot TEXT NOT NULL, physical_toggle TEXT NOT NULL DEFAULT 'UNKNOWN',
      used_days INT NOT NULL DEFAULT 0, clicks INT NOT NULL DEFAULT 0,
      PRIMARY KEY(device_id, slot)
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS pi_state (
      device_id UUID PRIMARY KEY REFERENCES pi_devices(id) ON DELETE CASCADE,
      active_slot TEXT, reset_day INT, emergency_stop BOOLEAN, uptime_seconds INT,
      cpu_temp FLOAT, disk_free_mb FLOAT, last_sync TIMESTAMPTZ, boot_count INT,
      last_shutdown_reason TEXT, clock_source TEXT, watchdog_enabled BOOLEAN,
      last_reboot_reason TEXT, config_version INT NOT NULL DEFAULT 0
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS pi_events (
      id SERIAL PRIMARY KEY, device_id UUID REFERENCES pi_devices(id) ON DELETE CASCADE,
      event_id TEXT UNIQUE, timestamp TIMESTAMPTZ, type TEXT, message TEXT
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS pi_commands (
      id UUID PRIMARY KEY, device_id UUID REFERENCES pi_devices(id) ON DELETE CASCADE,
      command TEXT NOT NULL, slot TEXT, params JSONB, status TEXT NOT NULL DEFAULT 'queued',
      created_at TIMESTAMPTZ, delivered_at TIMESTAMPTZ, started_at TIMESTAMPTZ,
      hardware_verified_at TIMESTAMPTZ, completed_at TIMESTAMPTZ, acked_at TIMESTAMPTZ,
      expires_at TIMESTAMPTZ, error TEXT, result TEXT
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS firmware_versions (
      version TEXT PRIMARY KEY, code TEXT NOT NULL, changelog TEXT, forced BOOLEAN NOT NULL DEFAULT FALSE,
      sha256 TEXT, signature TEXT, key_id TEXT, created_at TIMESTAMPTZ, updated_at TIMESTAMPTZ
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
      id SERIAL PRIMARY KEY, society_id INT, user_id INT, device_id UUID,
      action TEXT, details JSONB, created_at TIMESTAMPTZ
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS auth_sessions (
      jti UUID PRIMARY KEY, user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
      expires_at TIMESTAMPTZ NOT NULL, created_at TIMESTAMPTZ NOT NULL, revoked_at TIMESTAMPTZ
    )
    """)

    # Upgrade existing installations without application-startup DDL.
    op.execute("ALTER TABLE societies ADD COLUMN IF NOT EXISTS config_version INT NOT NULL DEFAULT 1")
    op.execute("ALTER TABLE societies ADD COLUMN IF NOT EXISTS reset_day INT NOT NULL DEFAULT 15")
    op.execute("UPDATE societies SET reset_day=15 WHERE reset_day IS NULL OR reset_day NOT BETWEEN 1 AND 28")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS config_version INT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'INVENTORY'")
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS hardware_profile TEXT NOT NULL DEFAULT 'EMS-4CH-v1'")
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS feedback_hardware_installed BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE slot_configs ADD COLUMN IF NOT EXISTS feedback_enabled BOOLEAN NOT NULL DEFAULT FALSE")
    op.execute("ALTER TABLE slot_state ADD COLUMN IF NOT EXISTS used_days INT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE slot_state ADD COLUMN IF NOT EXISTS clicks INT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE pi_commands ADD COLUMN IF NOT EXISTS started_at TIMESTAMPTZ")
    op.execute("ALTER TABLE pi_commands ADD COLUMN IF NOT EXISTS hardware_verified_at TIMESTAMPTZ")
    op.execute("ALTER TABLE pi_commands ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE firmware_versions ADD COLUMN IF NOT EXISTS sha256 TEXT")
    op.execute("ALTER TABLE firmware_versions ADD COLUMN IF NOT EXISTS signature TEXT")
    op.execute("ALTER TABLE firmware_versions ADD COLUMN IF NOT EXISTS key_id TEXT")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pi_commands_device_status_created ON pi_commands(device_id,status,created_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_auth_sessions_user ON auth_sessions(user_id)")
    op.execute("DROP INDEX IF EXISTS uq_pi_devices_society_id")


def downgrade():
    # Deliberately conservative: production rollback must use a forward migration.
    pass
