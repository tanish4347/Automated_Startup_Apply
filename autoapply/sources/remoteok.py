"""RemoteOK job source adapter."""

from typing import Any, Iterator
import json

from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient, html_to_text
from autoapply.logging import get_logger

log = get_logger(__name__)

class RemoteOkSource(BaseSource):
    def __init__(self, tags: list[str]):
        self.tags = tags

    @property
    def name(self) -> str:
        return "remoteok"

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        with HttpClient() as client:
            for t in self.tags:
                url = f"https://remoteok.com/api?tags={t}"
                log.info("remoteok_fetching", tag=t)
                resp = client.get(url)
                if not resp or resp.status_code != 200:
                    continue
                
                try:
                    jobs = resp.json()
                except json.JSONDecodeError:
                    continue
                    
                if not isinstance(jobs, list):
                    continue
                    
                for j in jobs:
                    if "legal" in j or not j.get("id"):
                        continue
                        
                    title = j.get("position", "")
                    if not title:
                        continue
                        
                    yield SourceResult(
                        title=title,
                        company=j.get("company", ""),
                        source=self.name,
                        source_id=str(j.get("id")),
                        source_url=j.get("url"),
                        application_url=j.get("apply_url") or j.get("url"),
                        location=j.get("location", ""),
                        description_raw=j.get("description", ""),
                        description_text=html_to_text(j.get("description", "")),
                        tags=j.get("tags", []),
                        raw_data=j
                    )

