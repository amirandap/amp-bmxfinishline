"""Alembic environment – wires up the app's SQLAlchemy metadata and DB URL."""

from __future__ import annotations

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make sure the project root is on sys.path so we can import app.*
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# Import models so their metadata is populated
from app.models import Base  # noqa: E402
from app.models import tables  # noqa: E402, F401 – registers ORM classes

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging – unless already configured.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    """Prefer DATABASE_URL env var, then app config, then alembic.ini."""
    url = os.environ.get("DATABASE_URL")
    if url:
        return url
    try:
        from app.config import get_config
        return get_config().database.url
    except Exception:
        pass
    return config.get_main_option("sqlalchemy.url", "sqlite:///data/finishline.db")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (no DBAPI connection required)."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    cfg_section = config.get_section(config.config_ini_section, {})
    cfg_section["sqlalchemy.url"] = _get_url()

    connectable = engine_from_config(
        cfg_section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
