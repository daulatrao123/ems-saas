"""Device credential lifecycle: per-device revocable/rotatable credentials.

pi_device_credentials replaces pi_devices.api_key_hash as the ONLY verifier consulted
by Pi authentication. Existing verifiers are migrated as 'legacy' credentials so
deployed devices keep working deterministically; the legacy column is left in place
(unused by auth) so 0001-0004 and device history are untouched.
"""
from alembic import op

revision = "0005_device_credentials"
down_revision = "0004_config_convergence"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS pi_device_credentials (
        id UUID PRIMARY KEY,
        device_id UUID NOT NULL REFERENCES pi_devices(id) ON DELETE CASCADE,
        key_id TEXT NOT NULL UNIQUE,
        secret_hash TEXT NOT NULL,
        hash_alg TEXT NOT NULL DEFAULT 'sha256',
        status TEXT NOT NULL DEFAULT 'active',
        origin TEXT NOT NULL DEFAULT 'rotate',
        created_at TIMESTAMPTZ NOT NULL,
        created_by INT,
        rotated_at TIMESTAMPTZ,
        revoked_at TIMESTAMPTZ,
        revoked_by INT,
        revoke_reason TEXT,
        CONSTRAINT ck_pi_device_credentials_status CHECK (status IN ('active','revoked')),
        CONSTRAINT ck_pi_device_credentials_revoked CHECK (
            (status = 'active' AND revoked_at IS NULL) OR (status = 'revoked' AND revoked_at IS NOT NULL)
        )
    )
    """)
    # Defence in depth: at most ONE active credential per device.
    op.execute("""
    CREATE UNIQUE INDEX IF NOT EXISTS ux_pi_device_credentials_one_active
        ON pi_device_credentials (device_id) WHERE status = 'active'
    """)
    op.execute("CREATE INDEX IF NOT EXISTS ix_pi_device_credentials_device ON pi_device_credentials (device_id)")

    # Backfill: every existing device verifier becomes a 'legacy' active credential
    # (unless the device is RETIRED — retired devices must not regain access).
    op.execute("""
    INSERT INTO pi_device_credentials (id, device_id, key_id, secret_hash, hash_alg, status, origin, created_at, revoked_at, revoke_reason)
    SELECT gen_random_uuid(), d.id, 'legacy-' || substr(d.id::text, 1, 8), d.api_key_hash, 'sha256',
           CASE WHEN upper(d.status) = 'RETIRED' THEN 'revoked' ELSE 'active' END, 'legacy', now(),
           CASE WHEN upper(d.status) = 'RETIRED' THEN now() END,
           CASE WHEN upper(d.status) = 'RETIRED' THEN 'DEVICE_RETIRED' END
    FROM pi_devices d
    WHERE d.api_key_hash IS NOT NULL AND d.api_key_hash <> ''
      AND NOT EXISTS (SELECT 1 FROM pi_device_credentials c WHERE c.device_id = d.id)
    """)


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ux_pi_device_credentials_one_active")
    op.execute("DROP INDEX IF EXISTS ix_pi_device_credentials_device")
    op.execute("DROP TABLE IF EXISTS pi_device_credentials")
