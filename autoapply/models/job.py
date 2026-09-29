"""Job model stores discovered job postings."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column, String, Text, Integer, Float, DateTime, Enum, Index, JSON,
)
from sqlalchemy.orm import relationship

from autoapply.models.base import Base

class WorkMode(str, enum.Enum):
    REMOTE = "remote"
    ONSITE = "onsite"
    HYBRID = "hybrid"
    UNKNOWN = "unknown"

class PayStatus(str, enum.Enum):
    PAID = "paid"
    UNPAID = "unpaid"
    UNKNOWN = "unknown"

class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(64), nullable=False, index=True)
    source_id = Column(String(512), nullable=True)
    source_url = Column(Text, nullable=True)
    application_url = Column(Text, nullable=True)
    title = Column(String(512), nullable=False)
    company = Column(String(256), nullable=True, index=True)
    location = Column(String(512), nullable=True)
    work_mode = Column(Enum(WorkMode), default=WorkMode.UNKNOWN)
    description_raw = Column(Text, nullable=True)
    description_text = Column(Text, nullable=True)
    responsibilities = Column(Text, nullable=True)
    requirements = Column(Text, nullable=True)
    preferred_qualifications = Column(Text, nullable=True)
    salary_min = Column(Float, nullable=True)
    salary_max = Column(Float, nullable=True)
    salary_currency = Column(String(16), nullable=True)
    compensation_text = Column(Text, nullable=True)
    pay_status = Column(Enum(PayStatus), default=PayStatus.UNKNOWN)
    pay_evidence = Column(Text, nullable=True)
    posted_date = Column(DateTime(timezone=True), nullable=True)
    deadline = Column(DateTime(timezone=True), nullable=True)
    discovered_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    ats_platform = Column(String(128), nullable=True)
    company_info = Column(Text, nullable=True)
    posting_snapshot = Column(Text, nullable=True)
    classification_category = Column(String(64), nullable=True)
    classification_evidence = Column(Text, nullable=True)
    raw_data = Column(JSON, nullable=True)
    is_active = Column(Integer, default=1, nullable=False)
    dedup_hash = Column(String(128), nullable=True, unique=True, index=True)
    tags = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    first_seen = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    last_seen = Column(DateTime(timezone=True), nullable=True, default=lambda: datetime.now(timezone.utc))
    closed_at = Column(DateTime(timezone=True), nullable=True)
    classification_status = Column(String(32), nullable=True)
    score = Column(Float, nullable=True)
    reject_reason = Column(Text, nullable=True)
    applications = relationship("Application", back_populates="job", lazy="selectin")
    __table_args__ = (
        Index("ix_jobs_source_source_id", "source", "source_id"),
        Index("ix_jobs_company_title", "company", "title"),
    )
