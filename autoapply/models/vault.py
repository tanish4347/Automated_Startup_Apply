from datetime import datetime, timezone
from sqlalchemy import Column, String, Text, Integer, Boolean, DateTime, ForeignKey
from sqlalchemy.orm import relationship as sa_relationship
from autoapply.models.base import Base

class VaultIdentity(Base):
    __tablename__ = "vault_identity"
    id = Column(Integer, primary_key=True, autoincrement=True)
    first_name = Column(String(128))
    middle_name = Column(String(128))
    last_name = Column(String(128))
    preferred_name = Column(String(128))
    pronouns = Column(String(64))
    email = Column(String(256))
    phone = Column(String(64))
    city = Column(String(128))
    country = Column(String(128))
    linkedin_url = Column(String(256))
    github_url = Column(String(256))
    portfolio_url = Column(String(256))
    codeforces_url = Column(String(256))
    leetcode_url = Column(String(256))
    kaggle_url = Column(String(256))
    google_scholar_url = Column(String(256))
    other_links = Column(Text)
    
    educations = sa_relationship("VaultEducation", back_populates="identity", cascade="all, delete-orphan")
    employments = sa_relationship("VaultEmployment", back_populates="identity", cascade="all, delete-orphan")
    projects = sa_relationship("VaultProject", back_populates="identity", cascade="all, delete-orphan")
    skills = sa_relationship("VaultSkill", back_populates="identity", cascade="all, delete-orphan")
    resumes = sa_relationship("VaultResume", back_populates="identity", cascade="all, delete-orphan")
    answers = sa_relationship("VaultAnswer", back_populates="identity", cascade="all, delete-orphan")

class VaultEducation(Base):
    __tablename__ = "vault_education"
    id = Column(Integer, primary_key=True, autoincrement=True)
    identity_id = Column(Integer, ForeignKey("vault_identity.id"))
    institution = Column(String(256))
    degree = Column(String(128))
    major = Column(String(128))
    minor = Column(String(128))
    start_date = Column(String(64))
    end_date = Column(String(64))
    cgpa = Column(String(64))
    scale = Column(String(64))
    
    identity = sa_relationship("VaultIdentity", back_populates="educations")

class VaultEmployment(Base):
    __tablename__ = "vault_employment"
    id = Column(Integer, primary_key=True, autoincrement=True)
    identity_id = Column(Integer, ForeignKey("vault_identity.id"))
    company = Column(String(256))
    job_title = Column(String(256))
    employment_type = Column(String(64))
    start_date = Column(String(64))
    end_date = Column(String(64))
    is_current = Column(Boolean, default=False)
    description = Column(Text)
    
    identity = sa_relationship("VaultIdentity", back_populates="employments")

class VaultProject(Base):
    __tablename__ = "vault_project"
    id = Column(Integer, primary_key=True, autoincrement=True)
    identity_id = Column(Integer, ForeignKey("vault_identity.id"))
    name = Column(String(256))
    description = Column(Text)
    technologies = Column(Text)
    github_url = Column(Text)
    demo_url = Column(Text)
    
    identity = sa_relationship("VaultIdentity", back_populates="projects")

class VaultSkill(Base):
    __tablename__ = "vault_skill"
    id = Column(Integer, primary_key=True, autoincrement=True)
    identity_id = Column(Integer, ForeignKey("vault_identity.id"))
    category = Column(String(128))
    name = Column(Text)
    
    identity = sa_relationship("VaultIdentity", back_populates="skills")

class VaultResume(Base):
    __tablename__ = "vault_resume"
    id = Column(Integer, primary_key=True, autoincrement=True)
    identity_id = Column(Integer, ForeignKey("vault_identity.id"))
    file_name = Column(String(256))
    file_path = Column(Text)
    uploaded_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    is_active = Column(Boolean, default=False)
    
    identity = sa_relationship("VaultIdentity", back_populates="resumes")

class VaultAnswer(Base):
    __tablename__ = "vault_answer"
    id = Column(Integer, primary_key=True, autoincrement=True)
    identity_id = Column(Integer, ForeignKey("vault_identity.id"))
    canonical_key = Column(String(128), index=True)
    category = Column(String(128))
    question = Column(Text)
    answer = Column(Text)
    source = Column(String(64), default="UNKNOWN")  # AUTO-FILLED FROM CV, USER ENTERED, AI GENERATED, etc.
    sensitivity = Column(String(64), default="NORMAL") # SENSITIVE, NORMAL
    status = Column(String(64), default="NEEDS REVIEW") # CONFIRMED, NEEDS REVIEW, UNKNOWN
    updated_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), onupdate=lambda: datetime.now(timezone.utc))
    
    identity = sa_relationship("VaultIdentity", back_populates="answers")

