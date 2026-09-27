import httpx
import logging
from typing import List, Dict, Any
from src.acquisition.base import BaseAcquisitionAdapter
from src.api.schemas import JobCreate

logger = logging.getLogger(__name__)

class RemotiveLiveAdapter(BaseAcquisitionAdapter):
    """
    Genuine LIVE adapter that fetches from Remotive's public unauthenticated API.
    """
    def __init__(self, mode: str = "live"):
        super().__init__(mode)
        self.base_url = "https://remotive.com/api/remote-jobs"
        self.attempted = 0
        self.succeeded = 0
        self.failed = 0
        self.limitations = "Public API rate limits. Returns only remote jobs."

    async def fetch_jobs(self, query: str = "") -> List[JobCreate]:
        if self.mode == "mock":
            return [] # This adapter is purely for live tests

        self.attempted += 1
        jobs = []

        # Remotive supports search terms.
        # Example query string mapping: 'Data Science Intern' -> 'data science'
        search_term = query.split()[0] if query else "software"

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"{self.base_url}?search={search_term}&limit=10")
                if resp.status_code == 200:
                    data = resp.json()
                    raw_jobs = data.get("jobs", [])

                    for rj in raw_jobs[:20]: # Grab up to 20 per query to avoid spamming the DB completely in one run
                        jobs.append(JobCreate(
                            source="remotive",
                            original_url=rj.get("url"),
                            company=rj.get("company_name", "Unknown"),
                            role=rj.get("title", "Unknown"),
                            location=rj.get("candidate_required_location", "Remote"),
                            work_mode="REMOTE",
                            description=rj.get("description", ""),
                            posting_date=rj.get("publication_date")
                        ))
                    self.succeeded += 1
                else:
                    self.failed += 1
                    self.limitations = f"LIVE SOURCE FAILED -> HTTP {resp.status_code}"
        except Exception as e:
            self.failed += 1
            self.limitations = f"LIVE SOURCE FAILED -> Network Error: {e}"
            logger.error(f"Remotive fetch failed: {e}")

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
