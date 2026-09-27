from abc import ABC, abstractmethod
from typing import List, Dict, Any
from src.api.schemas import JobCreate

class BaseAcquisitionAdapter(ABC):
    def __init__(self, mode: str = "mock"):
        self.mode = mode

    @abstractmethod
    async def fetch_jobs(self, query: str = "") -> List[JobCreate]:
        pass

    def get_status(self) -> Dict[str, Any]:
        return {
            "name": self.__class__.__name__,
            "mode": self.mode,
            "attempted": 0,
            "succeeded": 0,
            "failed": 0,
            "limitations": "None"
        }
