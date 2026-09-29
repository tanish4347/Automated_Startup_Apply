"""SQLAlchemy base and engine factory."""

from __future__ import annotations

from sqlalchemy import create_engine, event, Engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker, Session


class Base(DeclarativeBase):
    """Declarative base for all models."""
    pass


def engine_from_settings(db_url: str, echo: bool = False) -> Engine:
    """Create an engine from settings."""
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    engine = create_engine(db_url, echo=echo, connect_args=connect_args)
    if db_url.startswith("sqlite"):
        event.listen(engine, "connect", _sqlite_pragmas)
    return engine


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    """WAL lets the dashboard read while discovery writes; busy_timeout waits for locks instead of failing."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def get_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a session factory bound to an engine."""
    return sessionmaker(bind=engine, expire_on_commit=False)
