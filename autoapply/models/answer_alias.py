"""question_alias: normalised form label -> canonical_key (answer engine tier 1a)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text

from autoapply.models.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class QuestionAlias(Base):
    __tablename__ = "question_alias"

    id = Column(Integer, primary_key=True, autoincrement=True)
    alias = Column(String(1000), nullable=False, unique=True)    # questions.harvest.normalize_label(label)
    canonical_key = Column(String(128), nullable=False, index=True)
    source = Column(String(32), nullable=False)                  # report | harvest | embedding | review | new
    example = Column(Text, nullable=True)                        # one raw label as seen
    hits = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
