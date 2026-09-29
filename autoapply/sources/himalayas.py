"""Himalayas (himalayas.app) remote jobs via its public JSON API.

Two endpoints (probed 2026-09-30):
- GET /jobs/api/search?employment_type=Intern&page=N   (20 per page; an empty page ends it)
  Server-side filters: employment_type (Intern | Full Time | Part Time | Contractor | ...),
  country=<name>, worldwide=true, seniority=<level>, q=<text>. ~1,200 Intern jobs.
- GET /jobs/api?limit=20&cursor=<nextCursor>   the unfiltered feed (~94,000 jobs, limit capped
  at 20). Cursor pages never overlap. Off by default (feed_pages=0): too big to walk for the
  handful of internships in it, which the search endpoint already returns.

Every job is remote; locationRestrictions lists the countries a candidate must be in (empty =
worldwide), so it becomes the location: "Remote - United States" / "Remote (Worldwide)".
Descriptions come in full in both responses.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

from autoapply.ats.harvesters import parse_dt
from autoapply.logging import get_logger
from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient, html_to_text

log = get_logger(__name__)

BASE = "https://himalayas.app"

_PERIOD = {"hourly": "hour", "daily": "day", "weekly": "week", "monthly": "month", "annual": "year",
           "yearly": "year"}


def _salary_text(job: dict[str, Any]) -> str | None:
    lo, hi = job.get("minSalary"), job.get("maxSalary")
    if not (lo or hi):
        return None
    lo, hi = lo or hi, hi or lo
    amount = f"{lo:,}" if lo == hi else f"{lo:,} - {hi:,}"
    period = _PERIOD.get((job.get("salaryPeriod") or "").lower())
    return f"{job.get('currency') or ''} {amount}{f' /{period}' if period else ''}".strip()


def parse_job(job: dict[str, Any], now: datetime | None = None) -> SourceResult | None:
    title = (job.get("title") or "").strip()
    if not title:
        return None
    now = now or datetime.now(timezone.utc)
    countries = job.get("locationRestrictions") or []
    location = f"Remote - {', '.join(countries)}" if countries else "Remote (Worldwide)"
    expires = parse_dt(job.get("expiryDate"))
    url = job.get("applicationLink") or job.get("guid")
    return SourceResult(
        title=title,
        company=(job.get("companyName") or "").strip() or None,
        source="himalayas",
        source_id=job.get("guid") or url,
        source_url=job.get("guid") or url,
        application_url=url,
        location=location,
        work_mode="remote",
        description_raw=job.get("description"),
        description_text=html_to_text(job.get("description") or "") or job.get("excerpt"),
        salary_min=job.get("minSalary"),
        salary_max=job.get("maxSalary"),
        salary_currency=job.get("currency"),
        compensation_text=_salary_text(job),
        posted_date=parse_dt(job.get("pubDate")),
        deadline=expires,
        employment_type=job.get("employmentType"),
        tags=(job.get("categories") or []) + (job.get("seniority") or []) or None,
        raw_data={k: v for k, v in job.items() if k not in ("description", "timezoneRestrictions")},
        expired=bool(expires and expires < now),
        company_meta={"is_india": True} if countries == ["India"] else None,
    )


class HimalayasSource(BaseSource):
    def __init__(self, cfg: Any = None, http: HttpClient | None = None):
        from autoapply.config import HimalayasSearch
        self.cfg = cfg or HimalayasSearch()
        self._http = http

    @property
    def name(self) -> str:
        return "himalayas"

    def _search(self, client: HttpClient, seen: set[str]) -> Iterator[SourceResult]:
        for etype in self.cfg.employment_types:
            for page in range(1, self.cfg.max_pages + 1):
                resp = client.get(f"{BASE}/jobs/api/search", params={"employment_type": etype, "page": page})
                if resp is None or resp.status_code != 200:
                    log.warning("himalayas_page_failed", employment_type=etype, page=page,
                                status=getattr(resp, "status_code", None))
                    break
                data = resp.json() or {}
                jobs = data.get("jobs") or []
                log.info("himalayas_page", employment_type=etype, page=page, items=len(jobs), total=data.get("totalCount"))
                yield from self._emit(jobs, seen)
                if not jobs:
                    break

    def _feed(self, client: HttpClient, seen: set[str]) -> Iterator[SourceResult]:
        cursor = None
        for page in range(self.cfg.feed_pages):
            params = {"limit": 20, **({"cursor": cursor} if cursor else {})}
            resp = client.get(f"{BASE}/jobs/api", params=params)
            if resp is None or resp.status_code != 200:
                break
            data = resp.json() or {}
            yield from self._emit(data.get("jobs") or [], seen)
            cursor = data.get("nextCursor")
            if not cursor:
                break

    @staticmethod
    def _emit(jobs: list[dict[str, Any]], seen: set[str]) -> Iterator[SourceResult]:
        for job in jobs:
            key = job.get("guid") or job.get("applicationLink")
            if key in seen:
                continue
            seen.add(key)
            result = parse_job(job)
            if result:
                yield result

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        client = self._http or HttpClient(requests_per_second=1.0, headers={"Accept": "application/json"})
        seen: set[str] = set()
        try:
            yield from self._search(client, seen)
            yield from self._feed(client, seen)
        finally:
            if self._http is None:
                client.close()
