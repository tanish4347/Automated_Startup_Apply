import logging
from typing import List, Dict, Any
from src.acquisition.base import BaseAcquisitionAdapter
from src.api.schemas import JobCreate

logger = logging.getLogger(__name__)

class YCStartupJobsAdapter(BaseAcquisitionAdapter):
    def __init__(self, mode: str = "mock"):
        super().__init__(mode)
        self.attempted = 0
        self.succeeded = 0
        self.failed = 0
        self.limitations = "Requires Playwright/Auth. Mocked for test."

    async def fetch_jobs(self, query: str = "") -> List[JobCreate]:
        self.attempted += 1
        jobs = []
        if self.mode == "mock":
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
            self.succeeded += 1
        else:
            # LIVE MODE
            logger.info("YC Live mode requested. YC requires heavy auth and anti-bot bypass.")
            self.failed += 1
            self.limitations = "LIVE SOURCE FAILED -> authentication / anti-bot mechanisms prevent unauthenticated public scraping."

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
