from sqlalchemy import Column, String, Text, Integer, ForeignKey, JSON
from autoapply.models.base import Base

class ApplicationIntelligence(Base):
    __tablename__ = "application_intelligence"
    id = Column(Integer, primary_key=True, autoincrement=True)
    application_id = Column(Integer, ForeignKey("applications.id"))
    role_summary = Column(Text)
    company_summary = Column(Text)
    inferred_topics = Column(JSON)
    generated_questions = Column(JSON)
