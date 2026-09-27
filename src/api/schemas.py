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
