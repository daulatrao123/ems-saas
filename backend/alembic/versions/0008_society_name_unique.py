"""P1: society business identity must be unique among non-RETIRED societies.

Rule (mirrored by the API): lower(btrim(name)) is unique WHERE status <> 'RETIRED'.
This migration ENFORCES the invariant only; it never modifies tenant data. If duplicates
exist it aborts, listing the conflicting normalized names and society ids so an operator
can retire/rename the unintended duplicate through the Super Admin workflow first.
"""
from alembic import op
import sqlalchemy as sa

revision = "0008_society_name_unique"
down_revision = "0007_t8_operability"
branch_labels = None
depends_on = None

INDEX_NAME = "ux_societies_active_normalized_name"
DUPLICATE_SQL = """
    SELECT lower(btrim(name)) AS normalized_name, array_agg(id ORDER BY id) AS ids, count(*) AS n
    FROM societies
    WHERE status <> 'RETIRED'
    GROUP BY lower(btrim(name))
    HAVING count(*) > 1
    ORDER BY normalized_name
"""


def upgrade() -> None:
    rows = op.get_bind().execute(sa.text(DUPLICATE_SQL)).fetchall()
    if rows:
        listing = "; ".join(f"name={r[0]!r} ids={list(r[1])} count={r[2]}" for r in rows)
        raise RuntimeError(
            "MIGRATION 0008 ABORTED: duplicate non-RETIRED society names exist. No data was modified. "
            "Retire or rename the unintended duplicate(s) via Super Admin, then re-run `alembic upgrade head`. "
            f"Conflicts: {listing}"
        )
    op.execute(f"""
    CREATE UNIQUE INDEX IF NOT EXISTS {INDEX_NAME}
    ON societies (lower(btrim(name)))
    WHERE status <> 'RETIRED'
    """)


def downgrade() -> None:
    op.execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
