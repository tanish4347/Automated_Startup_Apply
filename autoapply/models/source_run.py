"""One row per source per discovery run: what it fetched, how many requests it spent, and whether
it ran cleanly. Daily request budgets (config/politeness.yaml) are summed from this table."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from autoapply.models.base import Base

# SourceRun.status
RUNNING, OK, DEGRADED, FAILED, SKIPPED = "running", "ok", "degraded", "failed", "skipped"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class SourceRun(Base):
    __tablename__ = "source_runs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(64), nullable=False, index=True)
    tier = Column(String(16), nullable=False, default="http")  # http | browser
    status = Column(String(16), nullable=False, default=RUNNING)
    # degraded: a CAPTCHA/challenge or block stopped the source; it was not solved or retried.
    started_at = Column(DateTime(timezone=True), nullable=False, default=_now, index=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    requests = Column(Integer, nullable=False, default=0)
    fetched = Column(Integer, nullable=False, default=0)
    new = Column(Integer, nullable=False, default=0)
    merged = Column(Integer, nullable=False, default=0)
    accepted = Column(Integer, nullable=False, default=0)
    failed = Column(Integer, nullable=False, default=0)
    error = Column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<SourceRun {self.source} {self.status} req={self.requests} new={self.new}>"
