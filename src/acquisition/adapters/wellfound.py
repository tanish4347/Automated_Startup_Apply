import logging
from typing import List
from src.acquisition.base import BaseAcquisitionAdapter
from src.api.schemas import JobCreate

logger = logging.getLogger(__name__)

class WellfoundAdapter(BaseAcquisitionAdapter):
    async def fetch_jobs(self) -> List[JobCreate]:
        logger.info("Fetching jobs from Wellfound...")
        jobs = []
        jobs.append(JobCreate(
            source="wellfound",
            original_url="https://wellfound.com/jobs/999",
            company="Wellfound Mock Startup",
            role="Frontend Developer",
            location="Remote",
            description="React frontend developer needed. Senior role.",
        ))
        return jobs
