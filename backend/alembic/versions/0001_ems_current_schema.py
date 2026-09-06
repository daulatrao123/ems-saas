"""Create and normalize the EMS relational schema.

This migration intentionally absorbs the legacy wing->slot naming transition so
application startup performs no DDL. It is safe for a fresh PostgreSQL database
and for the v5/v6 databases used by the EMS application.
"""
from alembic import op

revision = "0001_ems_current_schema"
down_revision = None
branch_labels = None
depends_on = None

DEFAULT_RESET_DAY = 15


def upgrade() -> None:
    # Normalize legacy table names before creating the current tables.
    op.execute("""
    DO $$ BEGIN
        IF to_regclass('public.wing_configs') IS NOT NULL THEN
            IF to_regclass('public.slot_configs') IS NULL THEN
                ALTER TABLE wing_configs RENAME TO slot_configs;
            ELSE
                ALTER TABLE wing_configs RENAME TO wing_configs_abandoned;
            END IF;
        END IF;
        IF to_regclass('public.wing_configs_old') IS NOT NULL THEN
            IF to_regclass('public.slot_configs') IS NULL THEN
                ALTER TABLE wing_configs_old RENAME TO slot_configs;
            ELSE
                ALTER TABLE wing_configs_old RENAME TO wing_configs_old_abandoned;
            END IF;
        END IF;
        IF to_regclass('public.wing_state') IS NOT NULL THEN
            IF to_regclass('public.slot_state') IS NULL THEN
                ALTER TABLE wing_state RENAME TO slot_state;
            ELSE
                ALTER TABLE wing_state RENAME TO wing_state_abandoned;
            END IF;
        END IF;
        IF to_regclass('public.wing_state_old') IS NOT NULL THEN
            IF to_regclass('public.slot_state') IS NULL THEN
                ALTER TABLE wing_state_old RENAME TO slot_state;
            ELSE
                ALTER TABLE wing_state_old RENAME TO wing_state_old_abandoned;
            END IF;
        END IF;
    END $$;
    """)

    op.execute("""
    CREATE TABLE IF NOT EXISTS societies (
        id SERIAL PRIMARY KEY,
        name TEXT,
        location TEXT,
        plan TEXT,
        status TEXT,
        tailscale_ip TEXT,
        pi_port INT,
        society_code TEXT,
        config_version INT DEFAULT 1,
        reset_day INT DEFAULT 15
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id SERIAL PRIMARY KEY,
        email TEXT UNIQUE,
        name TEXT,
        password TEXT,
        role TEXT,
        society_id INT
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS pi_devices (
        id UUID PRIMARY KEY,
        society_id INT,
        name TEXT,
        api_key_hash TEXT UNIQUE,
        firmware_version TEXT,
        last_seen TIMESTAMPTZ,
        status TEXT DEFAULT 'INVENTORY',
        hardware_profile TEXT DEFAULT 'EMS-4CH-v1',
        feedback_hardware_installed BOOLEAN DEFAULT FALSE
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS slot_configs (
        device_id UUID,
        slot TEXT,
        display_name TEXT,
        target_days INT,
        disabled BOOL DEFAULT FALSE,
        feedback_enabled BOOL DEFAULT FALSE,
        PRIMARY KEY (device_id, slot),
        FOREIGN KEY (device_id) REFERENCES pi_devices(id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS slot_state (
        device_id UUID,
        slot TEXT,
        physical_toggle TEXT DEFAULT 'UNKNOWN',
        used_days INT DEFAULT 0,
        clicks INT DEFAULT 0,
        PRIMARY KEY (device_id, slot),
        FOREIGN KEY (device_id) REFERENCES pi_devices(id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS pi_state (
        device_id UUID PRIMARY KEY,
        active_slot TEXT,
        reset_day INT,
        emergency_stop BOOL,
        uptime_seconds INT,
        cpu_temp FLOAT,
        disk_free_mb FLOAT,
        last_sync TIMESTAMPTZ,
        boot_count INT,
        last_shutdown_reason TEXT,
        clock_source TEXT,
        watchdog_enabled BOOL,
        last_reboot_reason TEXT,
        config_version INT DEFAULT 0,
        FOREIGN KEY (device_id) REFERENCES pi_devices(id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS pi_events (
        id SERIAL PRIMARY KEY,
        device_id UUID,
        event_id TEXT UNIQUE,
        timestamp TIMESTAMPTZ,
        type TEXT,
        message TEXT,
        FOREIGN KEY (device_id) REFERENCES pi_devices(id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS pi_commands (
        id UUID PRIMARY KEY,
        device_id UUID,
        command TEXT,
        slot TEXT,
        params JSONB,
        status TEXT DEFAULT 'queued',
        created_at TIMESTAMPTZ,
        delivered_at TIMESTAMPTZ,
        acked_at TIMESTAMPTZ,
        expires_at TIMESTAMPTZ,
        error TEXT,
        result TEXT,
        FOREIGN KEY (device_id) REFERENCES pi_devices(id) ON DELETE CASCADE
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS firmware_versions (
        version TEXT PRIMARY KEY,
        code TEXT,
        changelog TEXT,
        forced BOOL,
        created_at TIMESTAMPTZ,
        updated_at TIMESTAMPTZ
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS audit_log (
        id SERIAL PRIMARY KEY,
        society_id INT,
        user_id INT,
        device_id UUID,
        action TEXT,
        details JSONB,
        created_at TIMESTAMPTZ
    )
    """)
    op.execute("""
    CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
        id UUID PRIMARY KEY,
        user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        token_hash TEXT UNIQUE NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        created_at TIMESTAMPTZ NOT NULL,
        revoked_at TIMESTAMPTZ,
        replaced_by UUID REFERENCES auth_refresh_tokens(id)
    )
    """)

    # Normalize legacy column names and add current columns.
    op.execute("""
    DO $$ BEGIN
        IF to_regclass('public.slot_configs') IS NOT NULL THEN
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='slot_configs' AND column_name='wing_code') THEN
                ALTER TABLE slot_configs RENAME COLUMN wing_code TO slot;
            ELSIF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='slot_configs' AND column_name='slot_code') THEN
                ALTER TABLE slot_configs RENAME COLUMN slot_code TO slot;
            END IF;
        END IF;
        IF to_regclass('public.slot_state') IS NOT NULL THEN
            IF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='slot_state' AND column_name='wing_code') THEN
                ALTER TABLE slot_state RENAME COLUMN wing_code TO slot;
            ELSIF EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='slot_state' AND column_name='slot_code') THEN
                ALTER TABLE slot_state RENAME COLUMN slot_code TO slot;
            END IF;
        END IF;
        IF to_regclass('public.pi_commands') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pi_commands' AND column_name='wing') THEN
            ALTER TABLE pi_commands RENAME COLUMN wing TO slot;
        END IF;
        IF to_regclass('public.pi_state') IS NOT NULL AND EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name='pi_state' AND column_name='active_wing') THEN
            ALTER TABLE pi_state RENAME COLUMN active_wing TO active_slot;
        END IF;
    END $$;
    """)

    op.execute("ALTER TABLE societies ADD COLUMN IF NOT EXISTS config_version INT DEFAULT 1")
    op.execute("ALTER TABLE societies ADD COLUMN IF NOT EXISTS reset_day INT")
    op.execute("ALTER TABLE pi_state ADD COLUMN IF NOT EXISTS config_version INT DEFAULT 0")
    op.execute("ALTER TABLE pi_devices ALTER COLUMN society_id DROP NOT NULL")
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS status TEXT DEFAULT 'INVENTORY'")
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS hardware_profile TEXT DEFAULT 'EMS-4CH-v1'")
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS feedback_hardware_installed BOOLEAN DEFAULT FALSE")
    op.execute("ALTER TABLE slot_configs ADD COLUMN IF NOT EXISTS feedback_enabled BOOL DEFAULT FALSE")
    op.execute("ALTER TABLE slot_state ADD COLUMN IF NOT EXISTS used_days INT DEFAULT 0")
    op.execute("ALTER TABLE slot_state ADD COLUMN IF NOT EXISTS clicks INT DEFAULT 0")

    op.execute(f"UPDATE societies SET reset_day={DEFAULT_RESET_DAY} WHERE reset_day IS NULL OR reset_day < 1 OR reset_day > 28")
    op.execute(f"ALTER TABLE societies ALTER COLUMN reset_day SET DEFAULT {DEFAULT_RESET_DAY}")
    op.execute("ALTER TABLE societies ALTER COLUMN reset_day SET NOT NULL")
    op.execute("DROP INDEX IF EXISTS uq_pi_devices_society_id")

    op.execute("""
    DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_users_society') THEN
            ALTER TABLE users ADD CONSTRAINT fk_users_society
                FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE SET NULL;
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_devices_society') THEN
            ALTER TABLE pi_devices ADD CONSTRAINT fk_devices_society
                FOREIGN KEY (society_id) REFERENCES societies(id) ON DELETE SET NULL;
        END IF;
    END $$;
    """)


def downgrade() -> None:
    # Deliberately non-destructive: industrial deployments must not delete EMS history.
    raise RuntimeError("Downgrade is intentionally disabled; restore from a tested database backup instead.")
