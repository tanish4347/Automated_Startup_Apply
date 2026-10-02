"""Answer tier 3 cache: one generated draft per (canonical_key, company), so the same essay is never
regenerated (autoapply/answers/generate.py).

status: pending   passed verification; waits for the candidate's approval in /review. Filled into
                  forms for review, but an attempt carrying it always parks, even in live mode.
        approved  the candidate approved it (or edited it: draft is then their text). Usable.
        rejected  the verification pass found a claim not in the retrieved context. Parks; it is
                  not retried into something vaguer. A new draft is made only if the retrieved
                  context itself changes (context_digest: the story bank or the company brief).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, Integer, String, Text, UniqueConstraint

from autoapply.models.base import Base

PENDING, APPROVED, REJECTED = "pending", "approved", "rejected"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class GeneratedAnswer(Base):
    __tablename__ = "generated_answer"
    __table_args__ = (UniqueConstraint("canonical_key", "company_key", name="uq_generated_answer_key_company"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    canonical_key = Column(String(128), nullable=False)
    company_key = Column(String(256), nullable=False)        # normalize_brand(job.company)
    question = Column(Text, nullable=False)
    draft = Column(Text, nullable=True)
    status = Column(String(16), nullable=False, default=PENDING)
    context_digest = Column(String(40), nullable=False)      # sha1 of the retrieved passages
    context = Column(JSON, nullable=True)                    # [{id, source, text}] the draft was made from
    verification = Column(JSON, nullable=True)               # claims, their quotes, why rejected
    model = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    decided_at = Column(DateTime(timezone=True), nullable=True)
