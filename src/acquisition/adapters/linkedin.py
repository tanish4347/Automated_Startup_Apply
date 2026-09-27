import logging
from typing import List
from src.acquisition.base import BaseAcquisitionAdapter
from src.api.schemas import JobCreate

logger = logging.getLogger(__name__)

class LinkedInAdapter(BaseAcquisitionAdapter):
    async def fetch_jobs(self, query: str = "") -> List[JobCreate]:
        logger.info(f"Fetching from LinkedIn: {query}")
        jobs = []
        if "Remote ML Engineer" in query:
            jobs.append(JobCreate(
                source="linkedin", original_url="https://li/1", company="TechCo",
                role="Remote Junior ML Engineer", location="Remote",
                description="Entry level remote role using LLMs and Python.",
            ))
            jobs.append(JobCreate(
                source="linkedin", original_url="https://li/2", company="SeniorCo",
                role="Remote Senior ML Engineer", location="Remote",
                description="Lead team. 5+ years experience required.",
            ))
        elif "Full-Time Software Engineer" in query:
            jobs.append(JobCreate(
                source="linkedin", original_url="https://li/3", company="BangaloreTech",
                role="Software Engineer - Entry Level", location="Bangalore",
                description="Full-time onsite role.",
            ))
        return jobs
