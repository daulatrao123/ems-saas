"""T6: command idempotency, per-device sequencing and lease/attempt identity.

* idempotency_key (client-generated) + UNIQUE(device_id, idempotency_key); NULLs allowed for legacy clients.
* sequence_no: per-device monotonic, allocated from pi_devices.next_command_sequence under row lock.
* attempt_count + lifecycle timestamps for lease-safe CAS transitions.
* expires_at becomes mandatory (legacy NULL rows backfilled to created_at + 300s; migration aborts if any remain).
"""
from alembic import op
from sqlalchemy import text

revision = "0003_command_idempotency"
down_revision = "0002_auth_refresh_tokens"
branch_labels = None
depends_on = None

COMMAND_EXPIRY_SECONDS = 300


def upgrade() -> None:
    op.execute("ALTER TABLE pi_commands ADD COLUMN IF NOT EXISTS idempotency_key TEXT")
    op.execute("ALTER TABLE pi_commands ADD COLUMN IF NOT EXISTS sequence_no BIGINT")
    op.execute("ALTER TABLE pi_commands ADD COLUMN IF NOT EXISTS attempt_count INT NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE pi_commands ADD COLUMN IF NOT EXISTS executing_at TIMESTAMPTZ")
    op.execute("ALTER TABLE pi_commands ADD COLUMN IF NOT EXISTS hardware_verified_at TIMESTAMPTZ")
    op.execute("ALTER TABLE pi_commands ADD COLUMN IF NOT EXISTS completed_at TIMESTAMPTZ")
    op.execute("ALTER TABLE pi_devices ADD COLUMN IF NOT EXISTS next_command_sequence BIGINT NOT NULL DEFAULT 0")

    op.execute("""DO $$ BEGIN
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_pi_commands_attempt_nonneg') THEN
            ALTER TABLE pi_commands ADD CONSTRAINT ck_pi_commands_attempt_nonneg CHECK (attempt_count >= 0);
        END IF;
        IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='ck_pi_devices_next_seq_nonneg') THEN
            ALTER TABLE pi_devices ADD CONSTRAINT ck_pi_devices_next_seq_nonneg CHECK (next_command_sequence >= 0);
        END IF;
    END $$;""")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_pi_commands_device_idempotency ON pi_commands(device_id, idempotency_key)")
    op.execute("CREATE UNIQUE INDEX IF NOT EXISTS uq_pi_commands_device_sequence ON pi_commands(device_id, sequence_no)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_pi_commands_device_status_created ON pi_commands(device_id, status, created_at)")

    op.execute(f"""UPDATE pi_commands
                   SET expires_at = COALESCE(created_at, now()) + interval '{COMMAND_EXPIRY_SECONDS} seconds'
                   WHERE expires_at IS NULL""")
    remaining = op.get_bind().execute(text("SELECT count(*) FROM pi_commands WHERE expires_at IS NULL")).scalar()
    if remaining:
        raise RuntimeError(f"0003 aborted: {remaining} pi_commands rows still have NULL expires_at after backfill")
    op.execute("ALTER TABLE pi_commands ALTER COLUMN expires_at SET NOT NULL")

    # Seed the per-device allocator above any historical sequence values (all NULL on first run).
    op.execute("""UPDATE pi_devices d SET next_command_sequence = GREATEST(d.next_command_sequence,
                  COALESCE((SELECT max(sequence_no) FROM pi_commands c WHERE c.device_id = d.id), 0))""")


def downgrade() -> None:
    op.execute("ALTER TABLE pi_commands ALTER COLUMN expires_at DROP NOT NULL")
    op.execute("ALTER TABLE pi_commands DROP CONSTRAINT IF EXISTS ck_pi_commands_attempt_nonneg")
    op.execute("ALTER TABLE pi_devices DROP CONSTRAINT IF EXISTS ck_pi_devices_next_seq_nonneg")
    op.execute("DROP INDEX IF EXISTS idx_pi_commands_device_status_created")
    op.execute("DROP INDEX IF EXISTS uq_pi_commands_device_sequence")
    op.execute("DROP INDEX IF EXISTS uq_pi_commands_device_idempotency")
    op.execute("ALTER TABLE pi_devices DROP COLUMN IF EXISTS next_command_sequence")
    for col in ("completed_at", "hardware_verified_at", "executing_at", "attempt_count", "sequence_no", "idempotency_key"):
        op.execute(f"ALTER TABLE pi_commands DROP COLUMN IF EXISTS {col}")
