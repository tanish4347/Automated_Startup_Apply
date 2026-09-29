"""Alembic environment. The DB URL comes from autoapply settings, not alembic.ini."""

from alembic import context

import autoapply.models  # noqa: F401  (registers every table on Base.metadata)
from autoapply.config import get_settings
from autoapply.models.base import Base, engine_from_settings

config = context.config
target_metadata = Base.metadata


def _url() -> str:
    return config.attributes.get("db_url") or get_settings().db.url


def run_migrations_offline() -> None:
    context.configure(
        url=_url(), target_metadata=target_metadata, literal_binds=True,
        dialect_opts={"paramstyle": "named"}, render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = engine_from_settings(_url())
    with engine.connect() as connection:
        # render_as_batch: SQLite can't ALTER most things, so Alembic rebuilds tables.
        context.configure(connection=connection, target_metadata=target_metadata, render_as_batch=True)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
