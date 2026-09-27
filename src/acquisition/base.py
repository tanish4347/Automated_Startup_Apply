from abc import ABC, abstractmethod
from typing import List
from src.api.schemas import JobCreate

class BaseAcquisitionAdapter(ABC):
    @abstractmethod
    async def fetch_jobs(self, query: str = "") -> List[JobCreate]:
        pass
