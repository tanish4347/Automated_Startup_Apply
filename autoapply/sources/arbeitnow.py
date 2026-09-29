"""Arbeitnow job source adapter."""

from typing import Any, Iterator
import json

from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient, html_to_text
from autoapply.logging import get_logger

log = get_logger(__name__)


class ArbeitnowSource(BaseSource):
    @property
    def name(self) -> str:
        return "arbeitnow"

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        with HttpClient() as client:
            url = "https://arbeitnow.com/api/job-board-api"
            log.info("arbeitnow_fetching")
            
            resp = client.get(url)
            if not resp or resp.status_code != 200:
                log.warning("arbeitnow_fetch_failed", status=resp.status_code if resp else None)
                return
            
            try:
                data = resp.json()
                jobs = data.get("data", [])
            except json.JSONDecodeError:
                log.warning("arbeitnow_invalid_json")
                return
                
            for j in jobs:
                title = j.get("title", "")
                if not title:
                    continue
                    
                company = j.get("company_name", "")
                job_url = j.get("url")
                location = j.get("location", "")
                
                content_html = j.get("description", "")
                content_text = html_to_text(content_html)
                
                tags = j.get("tags", [])
                
                yield SourceResult(
                    title=title,
                    company=company,
                    source=self.name,
                    source_id=j.get("slug"),
                    source_url=job_url,
                    application_url=job_url,
                    location=location,
                    description_raw=content_html,
                    description_text=content_text,
                    tags=tags,
                    raw_data=j
                )
