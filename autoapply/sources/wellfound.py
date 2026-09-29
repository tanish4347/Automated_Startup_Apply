"""Wellfound (ex-AngelList Talent) from its server-rendered search pages.

Plain HTTP gets a 303. In a browser, the logged-out SEO landing pages render their results into
__NEXT_DATA__ as an Apollo cache (probed 2026-09-30); the pages make no JSON XHR of their own:
    /role/r/<role>              remote jobs for a role        (e.g. software-engineer: 1,842 jobs)
    /role/l/<role>/<location>   jobs for a role in a place    (software-engineer/india: 674)
    ?page=N                     20 startups per page; pageCount is in the results object
ROOT_QUERY.talent["seoLandingPageJobSearchResults({...})"] -> {totalJobCount, pageCount, startups}
StartupResult (name, slug, highConcept, companySize, highlightedJobListings) ->
JobListingSearchResult (title, jobType, locationNames, remote, acceptedRemoteLocationNames,
compensation, yearsExperienceMin, description (markdown), liveStartAt).

Logged out there is no internship filter (/jobs?jobTypes=internship is empty without a session)
and internships are rare on these pages (~1 in 40). The logged-in job search is a GraphQL call;
after `autoapply browser-login wellfound`, its requests are in data/browser_profiles/
wellfound.calls.jsonl, ready to be wired in here.
"""

from __future__ import annotations

import json
import re
from typing import Any, Iterator

from autoapply.ats.harvesters import parse_dt
from autoapply.logging import get_logger
from autoapply.sources.base import SourceResult
from autoapply.sources.browser import BrowserSource
from autoapply.sources.location import find_cities

log = get_logger(__name__)

BASE = "https://wellfound.com"
_NEXT_DATA = re.compile(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', re.S)


def page_url(role: str, location: str, page: int) -> str:
    path = f"/role/r/{role}" if location == "remote" else f"/role/l/{role}/{location}"
    return BASE + path + (f"?page={page}" if page > 1 else "")


def parse_page(html: str) -> tuple[list[SourceResult], int]:
    """Jobs on one results page and the page count (0 if the page had no results block)."""
    m = _NEXT_DATA.search(html or "")
    if not m:
        return [], 0
    data = ((json.loads(m.group(1)).get("props") or {}).get("pageProps") or {}).get("apolloState") or {}
    data = data.get("data", data)
    talent = (data.get("ROOT_QUERY") or {}).get("talent") or {}
    key = next((k for k in talent if k.startswith("seoLandingPageJobSearchResults")), None)
    if key is None:
        return [], 0
    res = talent[key]
    out = []
    for ref in res.get("startups") or []:
        startup = data.get(ref.get("__ref")) or {}
        for jref in startup.get("highlightedJobListings") or []:
            job = data.get(jref.get("__ref"))
            result = parse_job(job, startup) if job else None
            if result:
                out.append(result)
    return out, int(res.get("pageCount") or 0)


def parse_job(job: dict[str, Any], startup: dict[str, Any]) -> SourceResult | None:
    title = (job.get("title") or "").strip()
    if not title or not job.get("id"):
        return None
    kind = ((job.get("remoteConfig") or {}).get("kind") or "").upper()
    places = [p for p in job.get("locationNames") or [] if p]
    remote_ok = [p for p in job.get("acceptedRemoteLocationNames") or [] if p]
    if job.get("remote") or kind == "REMOTE":
        mode = "remote"
        location = f"Remote - {', '.join(remote_ok)}" if remote_ok else "Remote"
    else:
        mode = "hybrid" if kind == "HYBRID" else ("onsite" if places else "unknown")
        location = ", ".join(dict.fromkeys(places)) or None
    url = f"{BASE}/jobs/{job['id']}-{job.get('slug') or ''}".rstrip("-")
    exp = job.get("yearsExperienceMin")
    text = (f"Experience: {exp}+ years\n" if exp is not None else "") + (job.get("description") or "")
    india_cities = [c for p in places for c in find_cities(p)]
    job_type = job.get("jobType") or ""
    return SourceResult(
        title=title,
        company=(startup.get("name") or "").strip() or None,
        source="wellfound",
        source_id=str(job["id"]),
        source_url=url,
        application_url=url,
        location=location,
        work_mode=mode,
        description_text=text or None,
        compensation_text=job.get("compensation") or None,
        posted_date=parse_dt(job.get("liveStartAt")),
        employment_type="Internship" if job_type == "internship" else (job_type or None),
        company_info=startup.get("highConcept"),
        raw_data={k: v for k, v in job.items() if k != "description"},
        company_meta={
            "about": startup.get("highConcept"),
            "careers_url": f"{BASE}/company/{startup['slug']}/jobs" if startup.get("slug") else None,
            "is_india": bool(india_cities) or "India" in places,
            "india_cities": india_cities,
        },
    )


class WellfoundSource(BrowserSource):
    platform = "wellfound"

    def __init__(self, cfg: Any = None, **kw: Any):
        from autoapply.config import WellfoundSearch
        super().__init__(cfg or WellfoundSearch(), **kw)

    def harvest(self, session: Any) -> Iterator[SourceResult]:
        from playwright.sync_api import TimeoutError as PlaywrightTimeout
        seen: set[str] = set()
        for location in self.cfg.locations:
            for role in self.cfg.roles:
                for page in range(1, self.cfg.max_pages + 1):
                    try:
                        session.goto(page_url(role, location, page))
                    except PlaywrightTimeout as e:  # one slow page skips this search, not the source
                        log.warning("wellfound_page_timeout", role=role, location=location, page=page, error=str(e)[:80])
                        break
                    results, pages = parse_page(session.content())
                    log.info("wellfound_page", role=role, location=location, page=page, jobs=len(results),
                             pages=pages)
                    for r in results:
                        if r.source_id not in seen:
                            seen.add(r.source_id)
                            yield r
                    if not results or page >= pages:
                        break
