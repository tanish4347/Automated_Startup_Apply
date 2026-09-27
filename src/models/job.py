from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey
from sqlalchemy.orm import relationship
from datetime import datetime
from src.db.session import Base

class Job(Base):
    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, index=True)
    source = Column(String, index=True, nullable=False) # e.g. "linkedin", "yc"
    original_url = Column(String, unique=True, index=True, nullable=False)
    application_url = Column(String, nullable=True)

    company = Column(String, index=True, nullable=False)
    role = Column(String, index=True, nullable=False)
    location = Column(String, nullable=True)
    work_mode = Column(String, nullable=True) # Remote, Hybrid, Onsite

    compensation = Column(String, nullable=True)

    description = Column(Text, nullable=True)
    responsibilities = Column(Text, nullable=True)
    requirements = Column(Text, nullable=True)
    preferred_qualifications = Column(Text, nullable=True)

    posting_date = Column(DateTime, nullable=True)
    deadline = Column(DateTime, nullable=True)

    company_info = Column(Text, nullable=True)

    discovered_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    applications = relationship("Application", back_populates="job", cascade="all, delete-orphan")

class Application(Base):
    __tablename__ = "applications"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("jobs.id"), nullable=False)

    status = Column(String, index=True, default="QUEUED") # QUEUED, IN_PROGRESS, SUBMITTED, FAILED, etc.

    applied_at = Column(DateTime, nullable=True)

    resume_used = Column(String, nullable=True)
    answers_submitted = Column(Text, nullable=True) # JSON representation of answers

    confirmation_info = Column(Text, nullable=True)
    errors = Column(Text, nullable=True)

    job_snapshot = Column(Text, nullable=True) # JSON or text snapshot of JD at time of apply

    # Relationships
    job = relationship("Job", back_populates="applications")

class CandidateProfile(Base):
    __tablename__ = "candidate_profile"

    id = Column(Integer, primary_key=True, index=True)
    field_name = Column(String, unique=True, index=True, nullable=False)
    field_value = Column(Text, nullable=True)
