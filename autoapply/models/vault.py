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

    # ── Derived views used by the appliers ─────────────────────────────────
    # The appliers read a flat profile; these compute it from the Vault
    # tables so there is exactly one candidate store.

    @property
    def full_name(self) -> str | None:
        parts = [p for p in (self.first_name, self.middle_name, self.last_name) if p]
        return " ".join(parts) or None

    @property
    def location(self) -> str | None:
        parts = [p for p in (self.city, self.country) if p]
        return ", ".join(parts) or None

    @property
    def resume_path(self) -> str | None:
        active = next((r for r in self.resumes if r.is_active), None)
        return active.file_path if active else None

    def to_profile_dict(self) -> dict:
        """Everything the question engine may use, as JSON-able data.

        Only CONFIRMED answers are included, so unreviewed CV extractions and
        AI drafts never reach an employer form.
        """
        def compact(d: dict) -> dict:
            return {k: v for k, v in d.items() if v not in (None, "")}

        return {
            "identity": compact({
                c.name: getattr(self, c.name)
                for c in self.__table__.columns if c.name != "id"
            }),
            "education": [compact({
                "institution": e.institution, "degree": e.degree, "major": e.major,
                "minor": e.minor, "start_date": e.start_date, "end_date": e.end_date,
                "cgpa": e.cgpa, "scale": e.scale,
            }) for e in self.educations],
            "employment": [compact({
                "company": e.company, "job_title": e.job_title,
                "employment_type": e.employment_type, "start_date": e.start_date,
                "end_date": e.end_date, "is_current": e.is_current,
                "description": e.description,
            }) for e in self.employments],
            "projects": [compact({
                "name": p.name, "description": p.description,
                "technologies": p.technologies, "github_url": p.github_url,
                "demo_url": p.demo_url,
            }) for p in self.projects],
            "skills": [compact({"category": s.category, "name": s.name}) for s in self.skills],
            "answers": [
                {"question": a.question, "answer": a.answer}
                for a in self.answers if a.answer and a.status == "CONFIRMED"
            ],
        }

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


