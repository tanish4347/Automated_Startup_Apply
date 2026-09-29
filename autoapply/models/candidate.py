"""Candidate profile and pre-built answer store."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Column, String, Text, Integer, DateTime, JSON

from autoapply.models.base import Base


class CandidateProfile(Base):
    """Stores the candidate's personal information and documents."""
    __tablename__ = "candidate_profiles"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Personal
    full_name = Column(String(256), nullable=True)
    email = Column(String(256), nullable=True)
    phone = Column(String(64), nullable=True)
    location = Column(String(256), nullable=True)
    linkedin_url = Column(Text, nullable=True)
    portfolio_url = Column(Text, nullable=True)
    github_url = Column(Text, nullable=True)

    # Documents (paths or content)
    resume_path = Column(Text, nullable=True)
    resume_text = Column(Text, nullable=True)
    cover_letter_template = Column(Text, nullable=True)

    # Structured data
    education = Column(JSON, nullable=True)
    experience = Column(JSON, nullable=True)
    skills = Column(JSON, nullable=True)
    certifications = Column(JSON, nullable=True)
    projects = Column(JSON, nullable=True)

    # Additional fields (key-value)
    extra_fields = Column(JSON, nullable=True)

    # Metadata
    is_active = Column(Integer, default=1, nullable=False)
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

    def __repr__(self) -> str:
        return f"<CandidateProfile id={self.id} name={self.full_name!r}>"


class CandidateAnswer(Base):
    """Pre-built answers to common application questions."""
    __tablename__ = "candidate_answers"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Question matching
    question_pattern = Column(Text, nullable=False)
    question_category = Column(String(128), nullable=True, index=True)

    # Answer
    answer_text = Column(Text, nullable=False)
    answer_metadata = Column(JSON, nullable=True)

    # Priority (higher = preferred if multiple matches)
    priority = Column(Integer, default=0, nullable=False)

    # Metadata
    is_active = Column(Integer, default=1, nullable=False)
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

    def __repr__(self) -> str:
        return (
            f"<CandidateAnswer id={self.id} "
            f"category={self.question_category!r}>"
        )
