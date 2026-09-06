"""Server-side refresh-token store for HttpOnly cookie authentication (T4).

Each row is one opaque refresh token (stored as SHA-256 hash). Tokens issued by
rotation share a family_id; presenting an already-used or revoked token revokes
the whole family (refresh-token reuse detection).
"""
from alembic import op

revision = "0002_auth_refresh_tokens"
down_revision = "0001_ems_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS auth_refresh_tokens (
        id UUID PRIMARY KEY,
        user_id INT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        family_id UUID NOT NULL,
        token_hash TEXT NOT NULL UNIQUE,
        created_at TIMESTAMPTZ NOT NULL,
        expires_at TIMESTAMPTZ NOT NULL,
        used_at TIMESTAMPTZ,
        revoked_at TIMESTAMPTZ,
        replaced_by UUID
    )
    """)
    op.execute("CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_user ON auth_refresh_tokens(user_id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_auth_refresh_tokens_family ON auth_refresh_tokens(family_id)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS auth_refresh_tokens")
