from sqlalchemy import Column, Integer, String, Float, DateTime
from sqlalchemy.sql import func
from autoapply.models.base import Base

class ApprovedAnswerCache(Base):
    __tablename__ = "approved_answer_cache"
    id = Column(Integer, primary_key=True)
    canonical_concept = Column(String, index=True, nullable=False)
    answer = Column(String, nullable=False)
    confidence = Column(Float, default=1.0)
    source = Column(String)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
