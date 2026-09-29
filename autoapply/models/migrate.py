"""Programmatic Alembic entry points used by the CLI."""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from autoapply.models.base import engine_from_settings

MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"

# Schema that existed before Alembic was introduced (created with create_all).
BASELINE_REVISION = "0001_baseline"


def _config(db_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.attributes["db_url"] = db_url
    return cfg


def upgrade_db(db_url: str) -> None:
    """Bring the DB to the latest schema. A pre-Alembic DB is stamped at the baseline first."""
    tables = set(inspect(engine_from_settings(db_url)).get_table_names())
    cfg = _config(db_url)
    if "jobs" in tables and "alembic_version" not in tables:
        command.stamp(cfg, BASELINE_REVISION)
    command.upgrade(cfg, "head")
