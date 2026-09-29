"""Unstop (unstop.com) internships and jobs via its public search API.

GET https://unstop.com/api/public/opportunity/search-result
    opportunity=internships|jobs  oppstatus=open  page=N  per_page<=100
    job_type=wfh|in_office|hybrid  searchTerm=...  (also accepted: paid_unpaid, usertype,
    job_timing, datePosted=<days>, skills=<name>)

Findings from probing it (2026-09-30):
- Without oppstatus=open the endpoint mixes in expired listings and reports total=10000 (a cap);
  with it there are ~700 open internships and ~745 open jobs. So we always send oppstatus=open.
- There is no working category/domain filter: category=, domain=, location= are ignored (by
  name or by id), so we pull every open listing and let evaluate_job() filter.
- `region` is NOT the work mode: region=online covers in-office roles too. jobDetail.type
  (wfh | in_office | hybrid | on_field) is.
- jobDetail.pay_in defaults to "annually" even for monthly stipends (₹8,000-10,000 "annually");
  internship amounts under ₹1,00,000 are treated as monthly.
- The list response already carries the full description (`details`), so no detail fetch.
- regn_open alone doesn't mark a dead listing: oppstatus=expired items keep regn_open=1 with an
  end date in the past. A listing is expired if regn_open=0 or its registration end has passed.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterator

from autoapply.ats.harvesters import parse_dt
from autoapply.logging import get_logger
from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient, html_to_text

log = get_logger(__name__)

API = "https://unstop.com/api/public/opportunity/search-result"

_WORK_MODE = {"wfh": "remote", "in_office": "onsite", "on_field": "onsite", "hybrid": "hybrid"}


def _stipend_text(job: dict[str, Any], internship: bool) -> str | None:
    if not job:
        return None
    if job.get("paid_unpaid") == "unpaid":
        return "Unpaid"
    lo, hi = job.get("min_salary"), job.get("max_salary")
    if job.get("not_disclosed") or not (lo or hi):
        return None
    lo, hi = lo or hi, hi or lo
    period = "month" if job.get("pay_in") == "monthly" else "year"
    if period == "year" and internship and hi < 100_000:
        period = "month"
    amount = f"{lo:,}" if lo == hi else f"{lo:,} - {hi:,}"
    return f"INR {amount} /{period}"


def parse_item(item: dict[str, Any], now: datetime | None = None) -> SourceResult | None:
    title = (item.get("title") or "").strip()
    org = item.get("organisation") or {}
    if not title:
        return None
    job = item.get("jobDetail") or {}
    internship = item.get("subtype") == "internships"

    locs = item.get("locations") or []
    cities = [l.get("city") for l in locs if l.get("city")] or list(job.get("locations") or [])
    mode = _WORK_MODE.get(job.get("type") or "", "unknown")
    location = ", ".join(dict.fromkeys(cities)) or ("Remote" if mode == "remote" else None)
    india_cities = [l["city"] for l in locs if l.get("city") and l.get("country") == "India"]

    url = item.get("seo_url") or f"https://unstop.com/{item.get('public_url', '')}"
    regn = item.get("regnRequirements") or {}
    deadline = parse_dt(regn.get("end_regn_dt") or item.get("end_date"))
    now = now or datetime.now(timezone.utc)
    tags = [s.get("skill_name") or s.get("skill") for s in item.get("required_skills") or []]
    tags += [f["name"] for f in item.get("filters") or [] if f.get("type") in ("category", "domain")]
    raw = {k: v for k, v in item.items() if k not in ("details", "workfunction", "opportunity_config")}

    return SourceResult(
        title=title,
        company=(org.get("name") or "").strip() or None,
        source="unstop",
        source_id=str(item.get("id")),
        source_url=url,
        application_url=url,
        location=location,
        work_mode=mode,
        description_raw=item.get("details"),
        description_text=html_to_text(item.get("details") or "") or None,
        compensation_text=_stipend_text(job, internship),
        posted_date=parse_dt(regn.get("start_regn_dt")) or parse_dt(item.get("updated_at")),
        deadline=deadline,
        employment_type="Internship" if internship else (job.get("timing") or item.get("subtype")),
        tags=[t for t in dict.fromkeys(tags) if t],
        raw_data=raw,
        expired=item.get("regn_open") == 0 or bool(deadline and deadline < now),
        company_meta={"is_india": bool(india_cities), "india_cities": india_cities},
    )


class UnstopSource(BaseSource):
    def __init__(self, cfg: Any = None, http: HttpClient | None = None):
        from autoapply.config import UnstopSearch
        self.cfg = cfg or UnstopSearch()
        self._http = http

    @property
    def name(self) -> str:
        return "unstop"

    def _queries(self) -> Iterator[dict[str, Any]]:
        for opp in self.cfg.opportunities:
            for job_type in self.cfg.job_types or [None]:
                for term in self.cfg.search_terms or [None]:
                    q = {"opportunity": opp, "oppstatus": "open", "per_page": self.cfg.per_page}
                    if job_type:
                        q["job_type"] = job_type
                    if term:
                        q["searchTerm"] = term
                    yield q

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        client = self._http or HttpClient(requests_per_second=2.0)
        seen: set[str] = set()
        try:
            for query in self._queries():
                for page in range(1, self.cfg.max_pages + 1):
                    resp = client.get(API, params={**query, "page": page},
                                      headers={"Accept": "application/json"})
                    if resp is None or resp.status_code != 200:
                        log.warning("unstop_page_failed", query=query, page=page,
                                    status=getattr(resp, "status_code", None))
                        break
                    data = (resp.json() or {}).get("data") or {}
                    items = data.get("data") or []
                    for item in items:
                        key = str(item.get("id"))
                        if key in seen:
                            continue
                        seen.add(key)
                        result = parse_item(item)
                        if result:
                            yield result
                    log.info("unstop_page", query=query, page=page, items=len(items), total=data.get("total"))
                    if not items or not data.get("next_page_url"):
                        break
        finally:
            if self._http is None:
                client.close()
