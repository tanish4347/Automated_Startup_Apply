"""Greenhouse job source adapter."""

from typing import Any, Iterator
import json

from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient, html_to_text
from autoapply.logging import get_logger

log = get_logger(__name__)


class GreenhouseSource(BaseSource):
    def __init__(self, companies: list[str]):
        self.companies = companies

    @property
    def name(self) -> str:
        return "greenhouse"

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        with HttpClient() as client:
            for company in self.companies:
                url = f"https://boards-api.greenhouse.io/v1/boards/{company}/jobs?content=true"
                log.info("greenhouse_fetching", company=company)
                
                resp = client.get(url)
                if not resp or resp.status_code != 200:
                    log.warning("greenhouse_fetch_failed", company=company, status=resp.status_code if resp else None)
                    continue
                
                try:
                    data = resp.json()
                    jobs = data.get("jobs", [])
                except json.JSONDecodeError:
                    log.warning("greenhouse_invalid_json", company=company)
                    continue
                
                for j in jobs:
                    title = j.get("title", "")
                    if not title:
                        continue
                        
                    location = j.get("location", {}).get("name", "")
                    job_url = j.get("absolute_url")
                    content_html = j.get("content", "")
                    content_text = html_to_text(content_html)
                    
                    yield SourceResult(
                        title=title,
                        company=company,  # We use the slug as company name if real name isn't present
                        source=self.name,
                        source_id=str(j.get("id")),
                        source_url=job_url,
                        application_url=job_url,
                        location=location,
                        description_raw=content_html,
                        description_text=content_text,
                        ats_platform="greenhouse",
                        raw_data=j
                    )
