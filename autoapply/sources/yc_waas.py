"""YC Work at a Startup (workatastartup.com).

Plain HTTP gets a 406. The site is Rails + Inertia.js: each page's data is a JSON `data-page`
attribute, and the same JSON comes back from any URL requested with X-Inertia headers (probed
2026-09-30). Logged out, /jobs is a fixed teaser: 30 jobs (of totalJobsCount 2,855), identical for
every page= / role / filter parameter tried. The real, filterable job search only exists for a
signed-in candidate.

So this adapter reads the Inertia props of each configured path. Logged out that yields the
teaser and logs `yc_waas_logged_out`. Run `autoapply browser-login yc_waas` once to sign in: the
persistent profile keeps the session, and the signed-in search calls land in
data/browser_profiles/yc_waas.calls.jsonl, which is what the full search should be built from.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from typing import Any, Iterator

from autoapply.logging import get_logger
from autoapply.sources.base import SourceResult
from autoapply.sources.browser import BrowserSource
from autoapply.sources.http_client import guess_work_mode

log = get_logger(__name__)

BASE = "https://www.workatastartup.com"
_DATA_PAGE = re.compile(r'data-page="([^"]*)"')


def page_props(html: str) -> dict[str, Any]:
    m = _DATA_PAGE.search(html or "")
    return (json.loads(htmllib.unescape(m.group(1))).get("props") or {}) if m else {}


def logged_in(props: dict[str, Any]) -> bool:
    return bool(((props.get("nav") or {}).get("paths") or {}).get("logout"))


def parse_job(job: dict[str, Any]) -> SourceResult | None:
    title = (job.get("title") or "").strip()
    if not title or not job.get("id"):
        return None
    location = job.get("location") or None
    url = f"{BASE}/jobs/{job['id']}"
    tags = [t for t in (job.get("roleType"), job.get("companyBatch")) if t]
    return SourceResult(
        title=title,
        company=(job.get("companyName") or "").strip() or None,
        source="yc_waas",
        source_id=str(job["id"]),
        source_url=url,
        application_url=url,
        location=location,
        work_mode=guess_work_mode(location),
        compensation_text=job.get("salary") or None,
        employment_type=job.get("jobType"),
        company_info=job.get("companyOneLiner"),
        tags=tags or None,
        raw_data=job,
        company_meta={"about": job.get("companyOneLiner"),
                      "careers_url": f"{BASE}/companies/{job['companySlug']}" if job.get("companySlug") else None},
    )


class YcWaasSource(BrowserSource):
    platform = "yc_waas"

    def __init__(self, cfg: Any = None, **kw: Any):
        from autoapply.config import YcWaasSearch
        super().__init__(cfg or YcWaasSearch(), **kw)

    def harvest(self, session: Any) -> Iterator[SourceResult]:
        seen: set[str] = set()
        for path in self.cfg.paths:
            session.goto(BASE + path)
            props = page_props(session.content())
            if not logged_in(props):
                log.warning("yc_waas_logged_out", path=path, total=props.get("totalJobsCount"),
                            hint="run `autoapply browser-login yc_waas` for the full search")
            jobs = props.get("jobs") or []
            log.info("yc_waas_page", path=path, jobs=len(jobs), total=props.get("totalJobsCount"))
            for job in jobs:
                r = parse_job(job)
                if r and r.source_id not in seen:
                    seen.add(r.source_id)
                    yield r
