from typing import Iterator
import json
from pathlib import Path
from typing import Any

from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient
from autoapply.logging import get_logger

log = get_logger(__name__)


def _fetch_json(client: HttpClient, method: str, url: str, **kwargs: Any) -> Any | None:
    """Return parsed JSON for a 200 response, else None. 404 means 'no board here'."""
    resp = client.get(url, **kwargs) if method == "GET" else client.post(url, **kwargs)
    if resp is None or resp.status_code != 200:
        if resp is not None and resp.status_code != 404:
            log.warning("career_board_bad_status", url=url, status=resp.status_code)
        return None
    try:
        return resp.json()
    except ValueError:
        log.warning("career_board_invalid_json", url=url)
        return None

class CareerPagesSource(BaseSource):
    def __init__(self, companies_file: Path):
        self.companies_file = companies_file

    @property
    def name(self) -> str:
        return 'career_pages'

    def discover(self) -> Iterator[SourceResult]:
        if not self.companies_file.exists():
            log.error('career_pages_file_missing', path=str(self.companies_file))
            return
        with open(self.companies_file, 'r', encoding='utf-8') as f:
            companies = json.load(f)

        with HttpClient(requests_per_second=10.0, max_retries=1, timeout=10.0) as client:
            yield from self._scan(client, companies)

    def _scan(self, client: HttpClient, companies: list[dict]) -> Iterator[SourceResult]:
        for c in companies:
            name = c.get('company')
            board_token = c.get('board_token', name.lower().replace(' ', '').replace('-', ''))
            scanned = False
            
            # Greenhouse
            if not scanned:
                data = _fetch_json(client, "GET", f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs?content=true")
                if data is not None:
                    jobs = data.get('jobs', [])
                    log.info('greenhouse_board_found', company=name, count=len(jobs))
                    for j in jobs:
                        yield SourceResult(
                            title=j.get('title', ''), company=name, source=self.name,
                            location=j.get('location', {}).get('name', ''),
                            application_url=j.get('absolute_url', ''), description_text=j.get('content', ''),
                            description_raw=j.get('content', ''), ats_platform='greenhouse', source_id=str(j.get('id', ''))
                        )
                    scanned = True

            # Lever
            if not scanned:
                data = _fetch_json(client, "GET", f"https://api.lever.co/v0/postings/{board_token}")
                if isinstance(data, list):
                    jobs = data
                    log.info('lever_board_found', company=name, count=len(jobs))
                    for j in jobs:
                        yield SourceResult(
                            title=j.get('text', ''), company=name, source=self.name,
                            location=j.get('categories', {}).get('location', ''),
                            application_url=j.get('hostedUrl', ''), description_text=j.get('descriptionPlain', ''),
                            description_raw=j.get('description', ''), ats_platform='lever', source_id=str(j.get('id', ''))
                        )
                    scanned = True

            # Ashby
            if not scanned:
                data = _fetch_json(client, "GET", f"https://api.ashbyhq.com/posting-api/job-board/{board_token}")
                if data is not None:
                    jobs = data.get('jobs', [])
                    log.info('ashby_board_found', company=name, count=len(jobs))
                    for j in jobs:
                        yield SourceResult(
                            title=j.get('title', ''), company=name, source=self.name,
                            location=j.get('location', ''), application_url=j.get('jobUrl', ''),
                            description_text=j.get('descriptionHtml', ''), description_raw=j.get('descriptionHtml', ''),
                            ats_platform='ashby', source_id=str(j.get('id', ''))
                        )
                    scanned = True

            # SmartRecruiters
            if not scanned:
                data = _fetch_json(client, "GET", f"https://api.smartrecruiters.com/v1/companies/{board_token}/postings")
                if data is not None:
                    jobs = data.get('content', [])
                    log.info('smartrecruiters_board_found', company=name, count=len(jobs))
                    for j in jobs:
                        yield SourceResult(
                            title=j.get('name', ''), company=name, source=self.name,
                            location=j.get('location', {}).get('city', ''), application_url=f"https://jobs.smartrecruiters.com/{board_token}/{j.get('id')}",
                            description_text='', description_raw='',
                            ats_platform='smartrecruiters', source_id=str(j.get('id', ''))
                        )
                    scanned = True
                
            # Workable
            if not scanned:
                data = _fetch_json(client, "POST", f"https://apply.workable.com/api/v3/accounts/{board_token}/jobs", json={"query": "", "location": [], "department": [], "worktype": [], "remote": []})
                if data is not None:
                    jobs = data.get('results', [])
                    log.info('workable_board_found', company=name, count=len(jobs))
                    for j in jobs:
                        yield SourceResult(
                            title=j.get('title', ''), company=name, source=self.name,
                            location=j.get('location', {}).get('city', ''), application_url=f"https://apply.workable.com/{board_token}/j/{j.get('shortcode')}/",
                            description_text='', description_raw='',
                            ats_platform='workable', source_id=j.get('shortcode', '')
                        )
                    scanned = True

