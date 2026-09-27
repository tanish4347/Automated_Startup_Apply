from pydantic import BaseModel, HttpUrl
from typing import Optional, List
from datetime import datetime

class JobCreate(BaseModel):
    source: str
    original_url: str
    application_url: Optional[str] = None
    company: str
    role: str
    location: Optional[str] = None
    work_mode: Optional[str] = None
    compensation: Optional[str] = None
    description: Optional[str] = None
    responsibilities: Optional[str] = None
    requirements: Optional[str] = None
    preferred_qualifications: Optional[str] = None
    company_info: Optional[str] = None
    is_target_role: Optional[bool] = True
    role_type: Optional[str] = None
    filter_reason: Optional[str] = None
    filter_confidence: Optional[float] = None

    archived: bool = False
    search_run_id: Optional[int] = None
    target_field: Optional[str] = None
    seniority: Optional[str] = None
    experience_required: Optional[str] = None
    employment_type: Optional[str] = None
    relevance_score: Optional[float] = None



class JobResponse(JobCreate):
    id: int
    discovered_at: datetime

    class Config:
        from_attributes = True

class ApplicationResponse(BaseModel):
    id: int
    job_id: int
    status: str
    applied_at: Optional[datetime] = None

    class Config:
        from_attributes = True
