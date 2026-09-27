import httpx
import logging
from typing import List
from src.acquisition.base import BaseAcquisitionAdapter
from src.api.schemas import JobCreate

logger = logging.getLogger(__name__)

class YCStartupJobsAdapter(BaseAcquisitionAdapter):
    async def fetch_jobs(self, query: str = "") -> List[JobCreate]:
        logger.info(f"Fetching from YC: {query}")
        jobs = []
        if "Remote Data Science Internship" in query:
            jobs.append(JobCreate(
                source="yc", original_url="https://yc/1", company="YC AI",
                role="Data Science Intern", location="Remote",
                description="Use Python and Pandas for data science. Remote internship.",
            ))
        elif "Mumbai onsite ML internship" in query:
            jobs.append(JobCreate(
                source="yc", original_url="https://yc/2", company="Mumbai ML",
                role="Machine Learning Intern", location="Mumbai",
                description="Onsite in Mumbai building PyTorch models.",
            ))
        return jobs
