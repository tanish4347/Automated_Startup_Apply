"""Companies (the durable asset) and job sightings (every place a job was seen)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from autoapply.models.base import Base

ATS_TYPES = (
    "greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "keka",
    "zoho_recruit", "darwinbox", "freshteam", "google_form", "custom", "unknown",
)

# Company.priority
PRIORITY_LOW, PRIORITY_NORMAL, PRIORITY_HIGH = 0, 1, 2


def _now() -> datetime:
    return datetime.now(timezone.utc)


class Company(Base):
    __tablename__ = "companies"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(256), nullable=False)
    normalized_name = Column(String(256), nullable=False, unique=True, index=True)
    domain = Column(String(256), nullable=True, unique=True)
    careers_url = Column(Text, nullable=True)
    ats_type = Column(String(32), nullable=False, default="unknown")  # one of ATS_TYPES
    ats_token = Column(String(256), nullable=True)
    is_india = Column(Boolean, nullable=False, default=False)
    india_cities = Column(JSON, nullable=True)
    stage = Column(String(64), nullable=True)
    funding = Column(String(128), nullable=True)
    discovered_via = Column(String(64), nullable=True)
    priority = Column(Integer, nullable=False, default=PRIORITY_NORMAL)
    about_text = Column(Text, nullable=True)
    last_crawled_at = Column(DateTime(timezone=True), nullable=True)
    last_job_count = Column(Integer, nullable=True)
    crawl_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    jobs = relationship("Job", back_populates="company_ref")

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r} ats={self.ats_type}>"


class JobSighting(Base):
    """One row per (job, source, url): the same posting seen on LinkedIn, Internshala and the ATS."""
    __tablename__ = "job_sightings"
    __table_args__ = (UniqueConstraint("job_id", "source", "source_url", name="uq_job_sightings"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("jobs.id", name="fk_job_sightings_job_id"), nullable=False, index=True)
    source = Column(String(64), nullable=False)
    source_url = Column(Text, nullable=False, default="")  # '' not NULL, so the unique constraint holds
    first_seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    seen_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    job = relationship("Job", back_populates="sightings")
