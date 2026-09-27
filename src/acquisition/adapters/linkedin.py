import logging
from typing import List, Dict, Any
from src.acquisition.base import BaseAcquisitionAdapter
from src.api.schemas import JobCreate

logger = logging.getLogger(__name__)

class LinkedInAdapter(BaseAcquisitionAdapter):
    def __init__(self, mode: str = "mock"):
        super().__init__(mode)
        self.attempted = 0
        self.succeeded = 0
        self.failed = 0
        self.limitations = "Rate limited without proxy rotation."

    async def fetch_jobs(self, query: str = "") -> List[JobCreate]:
        self.attempted += 1
        jobs = []
        if self.mode == "mock":
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
            self.succeeded += 1
        else:
            # LIVE MODE
            logger.info("LinkedIn Live mode requested.")
            self.failed += 1
            self.limitations = "LIVE SOURCE FAILED -> CAPTCHA and rate limits on public unauthenticated search."
        return jobs

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "mode": self.mode,
            "attempted": self.attempted,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "limitations": self.limitations
        }
