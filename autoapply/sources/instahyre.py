"""Instahyre (instahyre.com) via its public job-search API.

GET https://www.instahyre.com/api/v1/job_search?experience_level=internship&limit=35&offset=N

Findings from probing it (2026-09-30):
- limit is capped at 35; meta.next is the next page's path (null on the last page).
- Filters that work: experience_level=internship|entry_level|associate|mid_senior|senior,
  years=<n> (years of experience), job_functions=<id> (9 = Data Science / ML, 10 = Backend),
  skills=<name>. q=, location=, job_type= do nothing or error.
- ~13,000 jobs in total but only ~63 internships.
- No description anywhere: /api/v1/job_search/<id> returns the same summary object, and the
  public job page answers 403 to non-browser clients. We store title, skills, locations and
  the employer, which is the part worth having: the embedded employer{} seeds `companies`.
"""

from __future__ import annotations

from typing import Any, Iterator

from autoapply.logging import get_logger
from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient
from autoapply.sources.location import find_cities

log = get_logger(__name__)

BASE = "https://www.instahyre.com"
PAGE_SIZE = 35

_EMPLOYMENT_TYPE = {"internship": "Internship"}


def parse_object(obj: dict[str, Any], experience_level: str | None = None) -> SourceResult | None:
    title = (obj.get("title") or obj.get("candidate_title") or "").strip()
    employer = obj.get("employer") or {}
    company = (employer.get("company_name") or "").strip()
    if not title or not company:
        return None
    locations = obj.get("locations") or ""
    remote = "work from home" in locations.lower() or "remote" in locations.lower()
    keywords = obj.get("keywords") or []
    about = " ".join(x for x in (employer.get("company_tagline"), employer.get("instahyre_note")) if x)
    return SourceResult(
        title=title,
        company=company,
        source="instahyre",
        source_id=str(obj.get("id")),
        source_url=obj.get("public_url"),
        application_url=obj.get("public_url"),
        location=locations or None,
        work_mode="remote" if remote else "unknown",
        description_text=f"Skills: {', '.join(keywords)}" if keywords else None,
        employment_type=_EMPLOYMENT_TYPE.get(experience_level or ""),
        company_info=about or None,
        tags=keywords or None,
        raw_data=obj,
        company_meta={
            "about": about or None,
            "india_cities": list(find_cities(locations)),
        },
    )


class InstahyreSource(BaseSource):
    def __init__(self, cfg: Any = None, http: HttpClient | None = None):
        from autoapply.config import InstahyreSearch
        self.cfg = cfg or InstahyreSearch()
        self._http = http

    @property
    def name(self) -> str:
        return "instahyre"

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        client = self._http or HttpClient(requests_per_second=1.0)
        seen: set[str] = set()
        try:
            for level in self.cfg.experience_levels:
                for page in range(self.cfg.max_pages):
                    params = {"experience_level": level, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE}
                    resp = client.get(f"{BASE}/api/v1/job_search", params=params,
                                      headers={"Accept": "application/json"})
                    if resp is None or resp.status_code != 200:
                        log.warning("instahyre_page_failed", params=params, status=getattr(resp, "status_code", None))
                        break
                    data = resp.json() or {}
                    objects = data.get("objects") or []
                    for obj in objects:
                        key = str(obj.get("id"))
                        if key in seen:
                            continue
                        seen.add(key)
                        result = parse_object(obj, level)
                        if result:
                            yield result
                    meta = data.get("meta") or {}
                    log.info("instahyre_page", level=level, offset=params["offset"], items=len(objects),
                             total=meta.get("total_count"))
                    if not objects or not meta.get("next"):
                        break
        finally:
            if self._http is None:
                client.close()
