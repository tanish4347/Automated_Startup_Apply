"""Naukri (naukri.com) through its own search API, called from inside a real page.

Plain HTTP gets 406 {"message":"recaptcha required"}. What the page itself sends (probed
2026-09-30) to GET /jobapi/v3/search?noOfResults=20&urlType=search_by_keyword&searchType=adv
&keyword=...&pageNo=N&src=directSearch:
    appid: 109    systemid: Naukri    clientid: d3skt0p    gid: LOCATION,INDUSTRY,EDUCATION,FAREA_ROLE
    nkparam: <token computed by the page's JS>    accept / content-type: application/json
Without nkparam the call is a 406 (recaptcha required). One nkparam, captured from the page's
own first search call, works for other keywords and filters in the same session, so a run loads
one listing page, captures the header set, then pages the API with fetch().

Filters that work: experience=<years> (0 = fresher), wfhType=2 (remote) / 3 (hybrid) / 0 (office),
cityTypeGid=134 (Mumbai). noOfResults is fixed at 20.

Details: /jobapi/v4/job/<id> wants its own nkparam, so instead the job page is opened and the
page's own v4 response captured. It carries the full description, experience range, wfhType and
applyRedirectUrl (often the company's ATS: a direct apply link). Details are fetched only for
jobs that pass evaluate_job(), and cached in data/cache/naukri/<jobId>.json.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlencode, urlsplit

from autoapply.ats.harvesters import parse_dt
from autoapply.logging import get_logger
from autoapply.sources.base import SourceResult
from autoapply.sources.browser import BrowserSource, ChallengeDetected
from autoapply.sources.http_client import html_to_text
from autoapply.sources.location import find_cities

log = get_logger(__name__)

BASE = "https://www.naukri.com"
BOOT_URL = f"{BASE}/internship-jobs"
SEARCH_PATH = "/jobapi/v3/search"
PAGE_SIZE = 20
# The subset of the page's request headers the API needs (cookies ride along with fetch()).
API_HEADERS = ("appid", "systemid", "clientid", "gid", "nkparam", "accept", "content-type")
_WFH = {"0": "onsite", "2": "remote", "3": "hybrid"}


def search_url(search: dict[str, Any], page: int) -> str:
    q = {"noOfResults": PAGE_SIZE, "urlType": "search_by_keyword", "searchType": "adv",
         "keyword": search["keyword"], "pageNo": page, "src": "directSearch"}
    for k in ("experience", "wfhType", "cityTypeGid"):
        if search.get(k) is not None:
            q[k] = search[k]
    return f"{BASE}{SEARCH_PATH}?{urlencode(q)}"


def _placeholders(job: dict[str, Any]) -> dict[str, str]:
    return {p.get("type"): p.get("label") or "" for p in job.get("placeholders") or []}


def _pay_text(label: str | None, internship: bool = False) -> str | None:
    label = (label or "").strip()
    if not label or label.lower() == "not disclosed":
        return None
    # "25,000/month" -> "INR 25,000 /month" so detect_pay reads the currency and period.
    if re.match(r"^[\d,.\s-]+/\s*\w+", label):
        amount, period = label.split("/", 1)
        return f"INR {amount.strip()} /{period.strip()}"
    # The detail API drops the period ("10,000"); Naukri internship stipends are monthly.
    if internship and re.fullmatch(r"[\d,.\s-]+", label):
        return f"INR {label} /month"
    return label


def _mode_from_text(location: str) -> str:
    t = location.lower()
    if "remote" in t or "work from home" in t:
        return "remote"
    if "hybrid" in t:
        return "hybrid"
    return "onsite" if t else "unknown"


def parse_job(job: dict[str, Any]) -> SourceResult | None:
    title = (job.get("title") or "").strip()
    if not title or not job.get("jobId"):
        return None
    ph = _placeholders(job)
    location = ph.get("location") or ""
    url = BASE + job["jdURL"] if job.get("jdURL") else f"{BASE}/job-listings-{job['jobId']}"
    lines = [f"Experience: {ph['experience']}" if ph.get("experience") else "",
             f"Duration: {ph['duration']}" if ph.get("duration") else "",
             html_to_text(job.get("jobDescription") or "")]
    job_type = job.get("jobType") or ""
    return SourceResult(
        title=title,
        company=(job.get("companyName") or "").strip() or None,
        source="naukri",
        source_id=str(job["jobId"]),
        source_url=url,
        application_url=url,
        location=location or None,
        work_mode=_mode_from_text(location),
        description_raw=job.get("jobDescription"),
        description_text="\n".join(l for l in lines if l) or None,
        compensation_text=_pay_text(ph.get("salary"), job_type == "internship"),
        employment_type="Internship" if job_type == "internship" else (job_type or None),
        raw_data={k: v for k, v in job.items() if k not in ("jobDescription", "ambitionBoxData", "logoPath",
                                                          "logoPathV3")},
        company_meta={"india_cities": list(find_cities(location))},
    )


def enrich(result: SourceResult, detail: dict[str, Any]) -> None:
    """Fold the job page's own /jobapi/v4/job response into a card-level result."""
    d = detail.get("jobDetails") or detail
    if d.get("description"):
        exp = ""
        if d.get("minimumExperience") is not None:
            exp = f"Experience: {d.get('minimumExperience')}-{d.get('maximumExperience')} years\n"
        result.description_raw = d["description"]
        result.description_text = exp + html_to_text(d["description"])
    locs = [l.get("label") for l in d.get("locations") or [] if l.get("label")]
    if locs:
        result.location = ", ".join(locs)
    mode = _WFH.get(str(d.get("wfhType")))
    if mode:
        result.work_mode = mode
    apply_url = d.get("applyRedirectUrl") or d.get("companyApplyUrl")
    if apply_url and "naukri.com" not in urlsplit(apply_url).netloc:
        result.application_url = apply_url
    result.posted_date = parse_dt(d.get("createdDate")) or result.posted_date
    salary = (d.get("salaryDetail") or {}).get("label")
    result.compensation_text = (_pay_text(salary, result.employment_type == "Internship")
                                or result.compensation_text)
    skills = [s.get("label") for group in (d.get("keySkills") or {}).values() if isinstance(group, list)
              for s in group if s.get("label")]
    result.tags = skills or result.tags
    company = d.get("companyDetail") or {}
    meta = dict(result.company_meta or {})
    meta["india_cities"] = list(dict.fromkeys((meta.get("india_cities") or [])
                                              + [c for l in locs for c in find_cities(l)]))
    if company.get("details"):
        meta["about"] = html_to_text(company["details"])
        result.company_info = meta["about"]
    if company.get("websiteUrl"):
        meta["domain"] = urlsplit(company["websiteUrl"]).netloc or None
    result.company_meta = meta


class NaukriSource(BrowserSource):
    platform = "naukri"

    def __init__(self, cfg: Any = None, *, search_cfg: Any = None, cache_dir: Path | None = None, **kw: Any):
        from autoapply.config import DATA_DIR, NaukriSearch
        super().__init__(cfg or NaukriSearch(), **kw)
        self.search_cfg = search_cfg
        self.cache_dir = cache_dir or DATA_DIR / "cache" / "naukri"
        self.stats = {"jobs": 0, "details_fetched": 0, "details_cached": 0, "details_skipped": 0}

    def _headers(self, session: Any) -> dict[str, str]:
        seen = session.goto(BOOT_URL, capture_request=SEARCH_PATH)
        headers = {k: v for k, v in (seen or {}).items() if k.lower() in API_HEADERS}
        if not headers.get("nkparam"):
            raise ChallengeDetected("naukri: the page's search call carried no nkparam")
        return headers

    def _passes_filter(self, result: SourceResult) -> bool:
        from autoapply.sources.filter import evaluate_job
        return evaluate_job(result.to_dict(), self.search_cfg)["classification_status"] != "AUTO_REJECT"

    def _detail(self, session: Any, result: SourceResult) -> dict[str, Any] | None:
        path = self.cache_dir / f"{result.source_id}.json"
        if path.exists():
            self.stats["details_cached"] += 1
            return json.loads(path.read_text(encoding="utf-8"))
        detail = session.goto(result.source_url, capture_response="/jobapi/v4/job/")
        if not detail:
            return None
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(detail.get("jobDetails") or detail, ensure_ascii=False), encoding="utf-8")
        self.stats["details_fetched"] += 1
        return detail

    def harvest(self, session: Any) -> Iterator[SourceResult]:
        headers = self._headers(session)
        seen: set[str] = set()
        for search in self.cfg.searches:
            for page in range(1, self.cfg.max_pages + 1):
                url = search_url(search, page)
                try:
                    data = session.fetch_json(url, headers=headers)
                except ChallengeDetected:
                    # nkparam can go stale mid-run: capture a fresh one once, then give up.
                    log.info("naukri_refresh_headers", keyword=search["keyword"], page=page)
                    headers = self._headers(session)
                    data = session.fetch_json(url, headers=headers)
                jobs = (data or {}).get("jobDetails") or []
                total = (data or {}).get("noOfJobs") or 0
                log.info("naukri_page", keyword=search["keyword"], page=page, jobs=len(jobs), total=total)
                for job in jobs:
                    result = parse_job(job)
                    if result is None or result.source_id in seen:
                        continue
                    seen.add(result.source_id)
                    self.stats["jobs"] += 1
                    if self.cfg.fetch_details and self._passes_filter(result):
                        detail = self._detail(session, result)
                        if detail:
                            enrich(result, detail)
                    else:
                        self.stats["details_skipped"] += 1
                    yield result
                if not jobs or page * PAGE_SIZE >= total:
                    break
        log.info("naukri_done", **self.stats)
