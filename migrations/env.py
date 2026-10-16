"""Alembic environment: uses the application's models and DATABASE_URL."""
import logging
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool
from sqlalchemy.engine import make_url

from app.config import get_settings
from app.database import Base
import app.models  # noqa: F401  (registers every table on Base.metadata)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Tests may pass an explicit URL via config attributes; otherwise use settings.
url = config.attributes.get("database_url") or get_settings().database_url

# Safety check: show which database will be changed (never the password) and refuse
# to migrate any database that does not belong to AssessFlow AI.
target_database = make_url(url).database or ""
logging.getLogger("alembic").info("Target database: %s", target_database)
if not target_database.startswith("assessflow"):
    raise RuntimeError(
        f"Refusing to run AssessFlow AI migrations against database '{target_database}'. "
        "Check DATABASE_URL in .env (expected assessflow_ai)."
    )

config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}), prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
