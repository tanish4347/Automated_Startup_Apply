from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, JSON
from sqlalchemy.orm import relationship
from datetime import datetime
from src.db.session import Base
from src.core.security import encrypt_value, decrypt_value

class CandidateProfileExt(Base):
    __tablename__ = "candidate_profile_ext"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String, nullable=False)
    last_name = Column(String, nullable=False)
    email = Column(String, nullable=False)
    phone = Column(String, nullable=True)
    summary = Column(Text, nullable=True)

    addresses = relationship("Address", back_populates="profile", cascade="all, delete-orphan")
    education = relationship("Education", back_populates="profile", cascade="all, delete-orphan")
    employment = relationship("Employment", back_populates="profile", cascade="all, delete-orphan")
    documents = relationship("Document", back_populates="profile", cascade="all, delete-orphan")
    sensitive_info = relationship("SensitiveInfo", back_populates="profile", uselist=False, cascade="all, delete-orphan")
    compliance = relationship("ComplianceInfo", back_populates="profile", uselist=False, cascade="all, delete-orphan")

class Address(Base):
    __tablename__ = "addresses"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("candidate_profile_ext.id"))
    type = Column(String) # Current, Permanent
    line1 = Column(String)
    line2 = Column(String, nullable=True)
    city = Column(String)
    state = Column(String)
    zip_code = Column(String)
    country = Column(String)

    profile = relationship("CandidateProfileExt", back_populates="addresses")

class Education(Base):
    __tablename__ = "education"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("candidate_profile_ext.id"))
    institution = Column(String)
    degree = Column(String)
    field = Column(String)
    start_date = Column(String)
    end_date = Column(String)
    gpa = Column(String, nullable=True)

    profile = relationship("CandidateProfileExt", back_populates="education")

class Employment(Base):
    __tablename__ = "employment"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("candidate_profile_ext.id"))
    company = Column(String)
    title = Column(String)
    start_date = Column(String)
    end_date = Column(String, nullable=True)
    current = Column(Boolean, default=False)
    description = Column(Text)

    profile = relationship("CandidateProfileExt", back_populates="employment")

class SensitiveInfo(Base):
    """
    Values in this table are ALWAYS encrypted at rest.
    """
    __tablename__ = "sensitive_info"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("candidate_profile_ext.id"))

    # These fields store the encrypted payload
    _pan_encrypted = Column("pan", String, nullable=True)
    _aadhaar_encrypted = Column("aadhaar", String, nullable=True)
    _passport_encrypted = Column("passport", String, nullable=True)

    @property
    def pan(self): return decrypt_value(self._pan_encrypted)
    @pan.setter
    def pan(self, value): self._pan_encrypted = encrypt_value(value)

    @property
    def aadhaar(self): return decrypt_value(self._aadhaar_encrypted)
    @aadhaar.setter
    def aadhaar(self, value): self._aadhaar_encrypted = encrypt_value(value)

    @property
    def passport(self): return decrypt_value(self._passport_encrypted)
    @passport.setter
    def passport(self, value): self._passport_encrypted = encrypt_value(value)

    profile = relationship("CandidateProfileExt", back_populates="sensitive_info")

class Document(Base):
    __tablename__ = "documents"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("candidate_profile_ext.id"))
    type = Column(String) # e.g., "10th certificate", "Resume", "PAN Card"
    file_path = Column(String) # Encrypted on disk
    metadata_json = Column(JSON, nullable=True)

    profile = relationship("CandidateProfileExt", back_populates="documents")

class ComplianceInfo(Base):
    __tablename__ = "compliance_info"
    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, ForeignKey("candidate_profile_ext.id"))
    has_government_family = Column(Boolean, default=False)
    has_conflict_of_interest = Column(Boolean, default=False)
    conflict_details = Column(Text, nullable=True)

    profile = relationship("CandidateProfileExt", back_populates="compliance")

class QuestionMemory(Base):
    __tablename__ = "question_memory"
    id = Column(Integer, primary_key=True, index=True)
    question = Column(String, index=True)
    normalized_question = Column(String, index=True)
    answer = Column(Text)
    answer_type = Column(String) # FACTUAL, PROFILE-DERIVED, ESSAY
    source = Column(String) # GENERATED, USER, CV
    date = Column(DateTime, default=datetime.utcnow)
    company = Column(String, nullable=True)
    role = Column(String, nullable=True)

class AuditTrail(Base):
    __tablename__ = "audit_trail"
    id = Column(Integer, primary_key=True, index=True)
    application_id = Column(Integer, ForeignKey("applications.id"))
    question = Column(String)
    answer = Column(Text)
    source = Column(String)
    generated = Column(Boolean, default=False)
    user_approved = Column(Boolean, default=True)
    timestamp = Column(DateTime, default=datetime.utcnow)
