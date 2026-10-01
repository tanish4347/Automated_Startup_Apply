"""Application model – tracks job application attempts and status."""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import (
    Column, String, Text, Integer, DateTime, Enum, ForeignKey, JSON,
)
from sqlalchemy.orm import relationship

from autoapply.models.base import Base


class ApplicationStatus(str, enum.Enum):
    DISCOVERED = "discovered"
    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    SUBMITTED = "submitted"
    ASSESSMENT = "assessment"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    FAILED = "failed"
    PARKED = "parked"   # a SENSITIVE answer is missing: waits for the candidate, never guessed
    CLOSED = "closed"


class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Foreign key to job
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False, index=True)

    # Denormalized for quick access
    company = Column(String(256), nullable=True)
    role = Column(String(512), nullable=True)

    # Status
    status = Column(
        Enum(ApplicationStatus),
        nullable=False,
        default=ApplicationStatus.DISCOVERED,
        index=True,
    )

    # URLs
    application_url = Column(Text, nullable=True)

    # Dates
    date_discovered = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    date_queued = Column(DateTime(timezone=True), nullable=True)
    date_applied = Column(DateTime(timezone=True), nullable=True)

    # Materials submitted
    resume_used = Column(Text, nullable=True)
    cover_letter_used = Column(Text, nullable=True)
    answers_submitted = Column(JSON, nullable=True)
    other_materials = Column(JSON, nullable=True)

    # Confirmation
    confirmation_id = Column(String(256), nullable=True)
    confirmation_text = Column(Text, nullable=True)
    confirmation_screenshot = Column(Text, nullable=True)

    # Errors
    error_message = Column(Text, nullable=True)
    error_details = Column(JSON, nullable=True)
    retry_count = Column(Integer, default=0, nullable=False)
    max_retries = Column(Integer, default=3, nullable=False)

    # Never double-apply: normalized company + title, shared by every job row of one posting
    # (autoapply/appliers/harness.py: cluster_key). Claimed in application_claims before an attempt.
    dedup_cluster = Column(String(512), nullable=True, index=True)
    # The daily cap counts applications whose last attempt started today (UTC), from this column.
    last_attempt_at = Column(DateTime(timezone=True), nullable=True, index=True)

    # Snapshot at time of application
    posting_snapshot_at_apply = Column(Text, nullable=True)

    # Timestamps
    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    job = relationship("Job", back_populates="applications")

    def __repr__(self) -> str:
        return (
            f"<Application id={self.id} job_id={self.job_id} "
            f"status={self.status!r}>"
        )
