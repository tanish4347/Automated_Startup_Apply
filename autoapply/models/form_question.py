"""Application-form questions harvested from real forms (autoapply/questions/harvest.py).

One row per (platform, company, raw label, field type): the same label on 40 postings of one
company is one row with times_seen=40, so ranking by distinct companies is not skewed by one
company's volume. distinct_companies is denormalised: how many companies on this platform ask a
question with the same normalised label. canonical_key is filled by clustering
(autoapply/questions/cluster.py).
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint

from autoapply.models.base import Base


def _now() -> datetime:
    return datetime.now(timezone.utc)


class FormQuestion(Base):
    __tablename__ = "form_question"
    __table_args__ = (UniqueConstraint("platform", "company_key", "raw_label", "field_type", name="uq_form_question"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    platform = Column(String(32), nullable=False, index=True)       # greenhouse | ashby | lever | internshala | ...
    canonical_key = Column(String(96), nullable=True, index=True)
    raw_label = Column(Text, nullable=False)
    field_type = Column(String(32), nullable=False)                # text | textarea | select | multi_select | boolean | file | ...
    is_required = Column(Boolean, nullable=False, default=False)
    options_json = Column(JSON, nullable=True)
    section = Column(String(32), nullable=True)                    # questions | demographic | location | system
    company_id = Column(Integer, ForeignKey("companies.id", name="fk_form_question_company_id"), nullable=True, index=True)
    company_key = Column(String(256), nullable=False)             # board token / platform name: the uniqueness scope
    job_id = Column(Integer, ForeignKey("jobs.id", name="fk_form_question_job_id"), nullable=True)
    example_url = Column(Text, nullable=True)
    times_seen = Column(Integer, nullable=False, default=1)
    distinct_companies = Column(Integer, nullable=False, default=1)
    first_seen = Column(DateTime(timezone=True), nullable=False, default=_now)
    last_seen = Column(DateTime(timezone=True), nullable=False, default=_now)
