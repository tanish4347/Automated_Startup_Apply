"""Remotive job source adapter."""

from typing import Any, Iterator
import json
import urllib.parse

from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient, html_to_text
from autoapply.logging import get_logger

log = get_logger(__name__)

class RemotiveSource(BaseSource):
    @property
    def name(self) -> str:
        return "remotive"

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        searches = ['software engineer intern', 'machine learning intern', 'intern', 'internship']
        
        with HttpClient() as client:
            for s in searches:
                q = urllib.parse.quote_plus(s)
                url = f"https://remotive.com/api/remote-jobs?search={q}"
                log.info("remotive_fetching", url=url)
                resp = client.get(url)
                if not resp or resp.status_code != 200:
                    continue
                
                try:
                    jobs = resp.json().get("jobs", [])
                except json.JSONDecodeError:
                    continue
                    
                for j in jobs:
                    title = j.get("title", "")
                    if not title:
                        continue
                    
                    yield SourceResult(
                        title=title,
                        company=j.get("company_name", ""),
                        source=self.name,
                        source_id=str(j.get("id")),
                        source_url=j.get("url"),
                        application_url=j.get("url"),
                        location=j.get("candidate_required_location", ""),
                        description_raw=j.get("description", ""),
                        description_text=html_to_text(j.get("description", "")),
                        tags=j.get("tags", []),
                        raw_data=j
                    )

