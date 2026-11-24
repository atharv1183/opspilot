"""Alembic environment: uses DATABASE_URL from opspilot settings and the ORM metadata."""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from opspilot.config import get_settings
from opspilot.db import models  # noqa: F401  (registers all tables on Base.metadata)
from opspilot.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

url = os.environ.get("ALEMBIC_DATABASE_URL") or get_settings().database_url
config.set_main_option("sqlalchemy.url", url)
target_metadata = Base.metadata


def include_name(name, type_, parent_names):  # noqa: ANN001, ANN201
    """Manage both the public (ERP) and rag schemas."""
    if type_ == "schema":
        return name in (None, "public", "rag")
    return True


def run_migrations_offline() -> None:
    """Emit SQL without a DB connection."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        include_schemas=True,
        include_name=include_name,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            include_schemas=True,
            include_name=include_name,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
