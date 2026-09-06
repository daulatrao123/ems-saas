from logging.config import fileConfig
import os
from sqlalchemy import engine_from_config, pool, text
from alembic import context

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

BASELINE_REVISION = "0001_ems_baseline"
# Retired root revisions that described the same baseline schema. A database
# stamped with one of these is adopted as BASELINE_REVISION instead of failing.
LEGACY_REVISIONS = (
    "0001_ems_current_schema",
    "0001_industrial_schema",
    "20260906_0001",
    "0001_industrial_baseline",
)


def normalize_database_url(raw: str) -> str:
    # Render/Heroku hand out postgres:// or postgresql://; force the psycopg3 driver.
    for prefix in ("postgres://", "postgresql://"):
        if raw.startswith(prefix):
            return "postgresql+psycopg://" + raw[len(prefix):]
    return raw


database_url = os.getenv("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL environment variable is required for Alembic migrations.")
database_url = normalize_database_url(database_url)
config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))

target_metadata = None


def adopt_legacy_revision(connection) -> None:
    exists = connection.execute(text("SELECT to_regclass('public.alembic_version')")).scalar()
    if exists is None:
        return
    current = connection.execute(text("SELECT version_num FROM alembic_version")).scalars().all()
    if len(current) == 1 and current[0] in LEGACY_REVISIONS:
        connection.execute(
            text("UPDATE alembic_version SET version_num = :new WHERE version_num = :old"),
            {"new": BASELINE_REVISION, "old": current[0]},
        )


def run_migrations_offline() -> None:
    context.configure(
        url=database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        adopt_legacy_revision(connection)
        connection.commit()
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
