"""Lever job source adapter."""

from typing import Any, Iterator
import json

from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient, html_to_text
from autoapply.logging import get_logger

log = get_logger(__name__)


class LeverSource(BaseSource):
    def __init__(self, companies: list[str]):
        self.companies = companies

    @property
    def name(self) -> str:
        return "lever"

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        with HttpClient() as client:
            for company in self.companies:
                url = f"https://api.lever.co/v0/postings/{company}?mode=json"
                log.info("lever_fetching", company=company)
                
                resp = client.get(url)
                if not resp or resp.status_code != 200:
                    log.warning("lever_fetch_failed", company=company, status=resp.status_code if resp else None)
                    continue
                
                try:
                    jobs = resp.json()
                except json.JSONDecodeError:
                    log.warning("lever_invalid_json", company=company)
                    continue
                
                if not isinstance(jobs, list):
                    continue

                for j in jobs:
                    title = j.get("text", "")
                    if not title:
                        continue
                        
                    categories = j.get("categories", {})
                    location = categories.get("location", "")
                    
                    job_url = j.get("hostedUrl")
                    apply_url = j.get("applyUrl")
                    
                    description_plain = j.get("descriptionPlain", "")
                    
                    # Construct full description from lists
                    full_desc = description_plain + "\n\n"
                    for lst in j.get("lists", []):
                        full_desc += lst.get("text", "") + "\n"
                        full_desc += lst.get("content", "") + "\n\n"
                    
                    yield SourceResult(
                        title=title,
                        company=company, 
                        source=self.name,
                        source_id=str(j.get("id")),
                        source_url=job_url,
                        application_url=apply_url,
                        location=location,
                        description_raw=None, # Lever v0 gives plain text mostly
                        description_text=full_desc.strip(),
                        ats_platform="lever",
                        raw_data=j
                    )
