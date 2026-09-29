"""Indeed RSS job source adapter."""

from typing import Any, Iterator
import urllib.parse
import xml.etree.ElementTree as ET

from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient, html_to_text
from autoapply.logging import get_logger

log = get_logger(__name__)


class IndeedRssSource(BaseSource):
    def __init__(self, keywords: list[str], locations: list[str]):
        self.keywords = keywords
        self.locations = locations

    @property
    def name(self) -> str:
        return "indeed_rss"

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        with HttpClient(requests_per_second=0.5) as client:
            for kw in self.keywords:
                for loc in self.locations:
                    # Construct RSS URL
                    q = urllib.parse.quote_plus(kw)
                    l = urllib.parse.quote_plus(loc)
                    url = f"https://www.indeed.com/rss?q={q}&l={l}"
                    
                    log.info("indeed_rss_fetching", keyword=kw, location=loc)
                    
                    resp = client.get(url)
                    if not resp or resp.status_code != 200:
                        log.warning("indeed_rss_fetch_failed", url=url, status=resp.status_code if resp else None)
                        continue
                        
                    try:
                        root = ET.fromstring(resp.text)
                    except ET.ParseError:
                        log.warning("indeed_rss_invalid_xml", url=url)
                        continue
                        
                    for item in root.findall(".//item"):
                        title = item.findtext("title", "")
                        if not title:
                            continue
                            
                        # Indeed RSS title is usually "Job Title - Company - Location"
                        company = ""
                        parts = title.split("-")
                        if len(parts) >= 2:
                            company = parts[1].strip()
                            title = parts[0].strip()
                            
                        job_url = item.findtext("link", "")
                        source_id = item.findtext("guid", "")
                        description_html = item.findtext("description", "")
                        description_text = html_to_text(description_html)
                        
                        yield SourceResult(
                            title=title,
                            company=company,
                            source=self.name,
                            source_id=source_id,
                            source_url=job_url,
                            application_url=job_url,
                            location=loc,
                            description_raw=description_html,
                            description_text=description_text,
                            raw_data={"rss_title": title, "keyword": kw, "location": loc}
                        )
