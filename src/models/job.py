from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Float
from sqlalchemy.orm import relationship
from datetime import datetime
from src.db.session import Base

class SearchRun(Base):
    __tablename__ = "search_runs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
    sources_queried = Column(Integer, default=0)
    queries_executed = Column(Integer, default=0)

    total_discovered = Column(Integer, default=0)
    duplicates_removed = Column(Integer, default=0)
    accepted = Column(Integer, default=0)
    rejected = Column(Integer, default=0)

    # Accepted breakdown
    remote_internships = Column(Integer, default=0)
    non_remote_internships = Column(Integer, default=0)
    remote_entry_level_ft = Column(Integer, default=0)

    # Rejection breakdown
    rej_wrong_field = Column(Integer, default=0)
    rej_non_remote_ft = Column(Integer, default=0)
    rej_senior = Column(Integer, default=0)
    rej_mumbai_non_remote = Column(Integer, default=0)
    rej_duplicate = Column(Integer, default=0)
    rej_other = Column(Integer, default=0)

    jobs = relationship("Job", back_populates="search_run")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, index=True, nullable=False)
    original_url = Column(String, unique=True, index=True, nullable=False)
    application_url = Column(String, nullable=True)

    company = Column(String, index=True, nullable=False)
    role = Column(String, index=True, nullable=False)
    location = Column(String, nullable=True)
    work_mode = Column(String, nullable=True) # REMOTE, HYBRID, ONSITE, UNKNOWN

    compensation = Column(String, nullable=True)

    description = Column(Text, nullable=True)
    responsibilities = Column(Text, nullable=True)
    requirements = Column(Text, nullable=True)
    preferred_qualifications = Column(Text, nullable=True)

    posting_date = Column(DateTime, nullable=True)
    deadline = Column(DateTime, nullable=True)
    company_info = Column(Text, nullable=True)

    discovered_at = Column(DateTime, default=datetime.utcnow)

    # State flags
    archived = Column(Boolean, default=False, index=True)
    search_run_id = Column(Integer, ForeignKey("search_runs.id"), nullable=True)
    filter_version = Column(String, default="1.0")

    # Classification Metadata
    is_target_role = Column(Boolean, default=False)
    employment_type = Column(String, nullable=True) # FULL_TIME, INTERNSHIP, CONTRACT
    role_type = Column(String, nullable=True)       # REMOTE_INTERNSHIP, NON_REMOTE_INTERNSHIP, REMOTE_ENTRY_LEVEL_FULL_TIME
    target_field = Column(String, nullable=True)
    seniority = Column(String, nullable=True)       # ENTRY_LEVEL, JUNIOR, SENIOR
    experience_required = Column(String, nullable=True)

    relevance_score = Column(Float, nullable=True)
    filter_reason = Column(Text, nullable=True)
    filter_confidence = Column(Float, nullable=True)

    # Relationships
    applications = relationship("Application", back_populates="job", cascade="all, delete-orphan")
    search_run = relationship("SearchRun", back_populates="jobs")


class Application(Base):
    __tablename__ = "applications"
    __table_args__ = {'extend_existing': True}

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)

    status = Column(String, index=True, default="QUEUED")

    applied_at = Column(DateTime, nullable=True)
    resume_used = Column(String, nullable=True)
    answers_submitted = Column(Text, nullable=True)

    confirmation_info = Column(Text, nullable=True)
    errors = Column(Text, nullable=True)
    job_snapshot = Column(Text, nullable=True)

    job = relationship("Job", back_populates="applications")
