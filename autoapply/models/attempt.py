"""Application attempts (the evidence of every run of an applier) and dedup-cluster claims.

ApplicationAttempt: one row per attempt. outcome is one of
  submitted  the platform's own success assertion matched (see each applier's success_assertion)
  uncertain  no error, but the assertion did not match: NOT a success
  parked     stopped before submitting: review_only mode (halted at the submit control, waiting
             for /review), or a SENSITIVE / free-text answer was missing
  failed     an error
review_status tracks the candidate's decision in /review: pending | approved | rejected.

ApplicationClaim: at most one claim per dedup cluster (UNIQUE). A claim is taken before an
attempt opens a form; a second application in the same cluster (the same internship found on
Internshala and on Unstop) cannot take one, so it is refused. Released only when an attempt
failed without submitting anything.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Column, DateTime, ForeignKey, Integer, String, Text

from autoapply.models.base import Base

SUBMITTED, UNCERTAIN, PARKED, FAILED = "submitted", "uncertain", "parked", "failed"
OUTCOMES = (SUBMITTED, UNCERTAIN, PARKED, FAILED)
REVIEW_ONLY, LIVE = "review_only", "live"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class ApplicationAttempt(Base):
    __tablename__ = "application_attempts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.id", name="fk_attempts_application_id"),
                            nullable=False, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id", name="fk_attempts_job_id"), nullable=False, index=True)
    platform = Column(String(32), nullable=False)
    mode = Column(String(16), nullable=False, default=REVIEW_ONLY)
    outcome = Column(String(16), nullable=True)            # submitted | uncertain | parked | failed
    park_reason = Column(Text, nullable=True)              # review_only | missing SENSITIVE key | free text ...
    started_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    screenshot_path = Column(Text, nullable=True)
    filled_fields = Column(JSON, nullable=True)            # [{label, canonical_key, value, tier, field_type}]
    unfilled_fields = Column(JSON, nullable=True)          # what could not be answered, and why
    final_url = Column(Text, nullable=True)
    form_fingerprint = Column(String(64), nullable=True, index=True)  # sha1(platform + sorted questions)
    success_evidence = Column(Text, nullable=True)         # what the success assertion saw
    error = Column(Text, nullable=True)
    blocked_requests = Column(JSON, nullable=True)         # non-GETs the read-only guard stopped
    review_status = Column(String(16), nullable=True, index=True)  # pending | approved | rejected
    review_note = Column(Text, nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)


class ApplicationClaim(Base):
    __tablename__ = "application_claims"

    id = Column(Integer, primary_key=True, autoincrement=True)
    dedup_cluster = Column(String(512), nullable=False, unique=True)
    application_id = Column(Integer, ForeignKey("applications.id", name="fk_claims_application_id"), nullable=False)
    claimed_at = Column(DateTime(timezone=True), nullable=False, default=_now)
