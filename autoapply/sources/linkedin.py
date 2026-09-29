"""LinkedIn Guest API (HTML) job source adapter."""

from typing import Any, Iterator
import urllib.parse
from bs4 import BeautifulSoup
import time

from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient
from autoapply.logging import get_logger

log = get_logger(__name__)

class LinkedInGuestSource(BaseSource):
    def __init__(self, keywords: list[str], locations: list[str], max_pages: int = 2):
        self.keywords = keywords
        self.locations = locations
        self.max_pages = max_pages

    @property
    def name(self) -> str:
        return "linkedin"

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        # Experience levels: 1 = Internship, 2 = Entry Level
        # Time posted: r86400 = past 24 hours, r604800 = past week
        # f_TPR=r604800&f_E=1
        
        with HttpClient(requests_per_second=1.0) as client:
            for kw in self.keywords:
                for loc in self.locations:
                    for page in range(self.max_pages):
                        start = page * 25
                        q = urllib.parse.quote_plus(kw)
                        l = urllib.parse.quote_plus(loc)
                        
                        url = f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords={q}&location={l}&f_TPR=r604800&f_E=1&start={start}"
                        
                        log.info("linkedin_fetching", keyword=kw, location=loc, start=start)
                        
                        resp = client.get(url)
                        if not resp or resp.status_code != 200 or not resp.text.strip():
                            break
                            
                        soup = BeautifulSoup(resp.text, "lxml")
                        job_cards = soup.find_all("li")
                        
                        if not job_cards:
                            break
                            
                        for card in job_cards:
                            title_elem = card.find("h3", class_="base-search-card__title")
                            company_elem = card.find("h4", class_="base-search-card__subtitle")
                            link_elem = card.find("a", class_="base-card__full-link")
                            loc_elem = card.find("span", class_="job-search-card__location")
                            
                            if not title_elem or not link_elem:
                                continue
                                
                            title = title_elem.get_text(strip=True)
                            company = company_elem.get_text(strip=True) if company_elem else ""
                            job_url = link_elem.get("href", "")
                            job_url = job_url.split("?")[0] if job_url else ""
                            
                            source_id = ""
                            if "view/" in job_url:
                                parts = job_url.split("view/")
                                if len(parts) > 1:
                                    source_id = parts[1].strip("/")
                            
                            location = loc_elem.get_text(strip=True) if loc_elem else loc
                            
                            yield SourceResult(
                                title=title,
                                company=company,
                                source=self.name,
                                source_id=source_id,
                                source_url=job_url,
                                application_url=job_url,
                                location=location,
                                raw_data={"keyword": kw}
                            )

