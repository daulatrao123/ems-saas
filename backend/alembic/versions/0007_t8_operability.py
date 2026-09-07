"""T8: bounded archival of terminal pi_commands (auditability preserved).

Archive mirrors pi_commands' columns at creation (LIKE ... INCLUDING DEFAULTS) plus
archived_at. No extra indexes: the archive is write-mostly and read by id/device only.
"""
from alembic import op
import sqlalchemy as sa

revision = "0007_t8_operability"
down_revision = "0006_signed_ota"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE IF NOT EXISTS pi_commands_archive (
        LIKE pi_commands INCLUDING DEFAULTS,
        archived_at TIMESTAMPTZ NOT NULL DEFAULT now(),
        PRIMARY KEY (id)
    )
    """)


def downgrade() -> None:
    # Non-destructive: move archived history back into pi_commands before dropping the table.
    bind = op.get_bind()
    cols = [r[0] for r in bind.execute(sa.text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='pi_commands' ORDER BY ordinal_position"
    ))]
    col_list = ", ".join(f'"{c}"' for c in cols)
    op.execute(f"INSERT INTO pi_commands ({col_list}) SELECT {col_list} FROM pi_commands_archive ON CONFLICT (id) DO NOTHING")
    op.execute("DROP TABLE IF EXISTS pi_commands_archive")
