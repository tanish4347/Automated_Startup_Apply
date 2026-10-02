"""Companies (the durable asset) and job sightings (every place a job was seen)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON, Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import relationship

from autoapply.models.base import Base

ATS_TYPES = (
    "greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "keka",
    "zoho_recruit", "darwinbox", "freshteam", "mynexthire", "workday", "turbohire", "bamboohr",
    "jazzhr", "personio", "teamtailor", "breezy", "pinpoint", "recruiterflow", "skillate",
    "springrecruit", "hirepro", "zwayam", "google_form", "custom", "unknown",
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
    website = Column(Text, nullable=True)       # full homepage URL, as the seed gave it
    careers_url = Column(Text, nullable=True)
    ats_type = Column(String(32), nullable=False, default="unknown")  # one of ATS_TYPES
    ats_token = Column(String(256), nullable=True)
    # ATS resolution (discovery/ats_resolver.py): resolved at most once per 30 days on success,
    # exponential backoff on failure, handed to the generic crawler after 4 failures.
    ats_confidence = Column(Float, nullable=True)       # 0-1
    ats_evidence = Column(Text, nullable=True)          # how it was resolved
    ats_resolved_at = Column(DateTime(timezone=True), nullable=True)
    ats_last_attempt_at = Column(DateTime(timezone=True), nullable=True)
    ats_resolve_failures = Column(Integer, nullable=False, default=0)
    needs_generic_crawl = Column(Boolean, nullable=False, default=False)
    is_india = Column(Boolean, nullable=False, default=False)
    india_cities = Column(JSON, nullable=True)
    stage = Column(String(64), nullable=True)
    funding = Column(String(128), nullable=True)
    discovered_via = Column(String(64), nullable=True)
    seed_sources = Column(JSON, nullable=True)  # every universe seeder that produced it
    priority = Column(Integer, nullable=False, default=PRIORITY_NORMAL)
    about_text = Column(Text, nullable=True)
    # Fetched once from the company's own site, cached permanently (services/company_brief.py);
    # shared by answer tier 3 and interview prep.
    company_brief = Column(JSON, nullable=True)
    company_brief_at = Column(DateTime(timezone=True), nullable=True)
    last_crawled_at = Column(DateTime(timezone=True), nullable=True)
    last_job_count = Column(Integer, nullable=True)
    crawl_error = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    jobs = relationship("Job", back_populates="company_ref")

    def __repr__(self) -> str:
        return f"<Company id={self.id} name={self.name!r} ats={self.ats_type}>"


class CompanyAlias(Base):
    """A spelling (or domain) that was merged into a company. One row per merge, so false merges
    can be audited: `autoapply universe merges`."""
    __tablename__ = "company_aliases"
    __table_args__ = (UniqueConstraint("company_id", "alias", "seed_source", name="uq_company_aliases"),)

    id = Column(Integer, primary_key=True, autoincrement=True)
    company_id = Column(Integer, ForeignKey("companies.id", name="fk_company_aliases_company_id"),
                        nullable=False, index=True)
    alias = Column(String(256), nullable=False)
    seed_source = Column(String(64), nullable=False)
    match_type = Column(String(16), nullable=False)  # name | domain
    evidence = Column(Text, nullable=True)           # e.g. the matching domain
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    company = relationship("Company")


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
