"""Adzuna India via its official API (needs a free key from https://developer.adzuna.com/).

GET https://api.adzuna.com/v1/api/jobs/in/search/<page>
    app_id, app_key, results_per_page<=50, what=<keywords>, max_days_old=<n>, sort_by=date

Credentials come from ADZUNA_APP_ID / ADZUNA_APP_KEY (environment or .env). Without them the
source logs one line and yields nothing, so a run never breaks on it.

Not verified live: no key was available when this was written (without one the API answers with
an HTML error page). The parser follows the documented response shape; the test fixture is
hand-written to that shape. `description` is Adzuna's ~500-char snippet, and redirect_url goes
through Adzuna to the employer's page. salary_is_predicted=1 means Adzuna estimated the salary,
so it is not stored.
"""

from __future__ import annotations

import os
from typing import Any, Iterator

from autoapply.ats.harvesters import parse_dt
from autoapply.logging import get_logger
from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient

log = get_logger(__name__)

API = "https://api.adzuna.com/v1/api/jobs/in/search/{page}"


def credentials() -> tuple[str, str] | None:
    app_id, app_key = os.environ.get("ADZUNA_APP_ID"), os.environ.get("ADZUNA_APP_KEY")
    return (app_id, app_key) if app_id and app_key else None


def parse_result(r: dict[str, Any]) -> SourceResult | None:
    title = (r.get("title") or "").strip()
    if not title:
        return None
    location = r.get("location") or {}
    area = location.get("area") or []
    predicted = str(r.get("salary_is_predicted")) == "1"
    lo, hi = (None, None) if predicted else (r.get("salary_min"), r.get("salary_max"))
    comp = None
    if lo or hi:
        lo, hi = lo or hi, hi or lo
        comp = f"INR {lo:,.0f}" + (f" - {hi:,.0f}" if hi != lo else "") + " /year"
    contract = " ".join(x for x in (r.get("contract_time"), r.get("contract_type")) if x) or None
    return SourceResult(
        title=title,
        company=((r.get("company") or {}).get("display_name") or "").strip() or None,
        source="adzuna",
        source_id=str(r.get("id")),
        source_url=r.get("redirect_url"),
        application_url=r.get("redirect_url"),
        location=location.get("display_name"),
        description_text=r.get("description"),
        salary_min=lo,
        salary_max=hi,
        salary_currency="INR" if (lo or hi) else None,
        compensation_text=comp,
        posted_date=parse_dt(r.get("created")),
        employment_type=contract,
        tags=[(r.get("category") or {}).get("label")] if (r.get("category") or {}).get("label") else None,
        raw_data=r,
        company_meta={"india_cities": area[2:3] if len(area) > 2 else []},
    )


class AdzunaSource(BaseSource):
    def __init__(self, cfg: Any = None, http: HttpClient | None = None):
        from autoapply.config import AdzunaSearch
        self.cfg = cfg or AdzunaSearch()
        self._http = http

    @property
    def name(self) -> str:
        return "adzuna"

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        creds = credentials()
        if creds is None:
            log.info("adzuna_skipped", reason="ADZUNA_APP_ID / ADZUNA_APP_KEY not set")
            return
        app_id, app_key = creds
        client = self._http or HttpClient(requests_per_second=1.0, headers={"Accept": "application/json"})
        seen: set[str] = set()
        try:
            for query in self.cfg.queries:
                for page in range(1, self.cfg.max_pages + 1):
                    params = {"app_id": app_id, "app_key": app_key, "what": query, "sort_by": "date",
                              "results_per_page": self.cfg.results_per_page,
                              "max_days_old": self.cfg.max_days_old}
                    resp = client.get(API.format(page=page), params=params)
                    if resp is None or resp.status_code != 200:
                        # Never log params: they carry the key.
                        log.warning("adzuna_page_failed", query=query, page=page,
                                    status=getattr(resp, "status_code", None))
                        break
                    data = resp.json() or {}
                    results = data.get("results") or []
                    for r in results:
                        key = str(r.get("id"))
                        if key in seen:
                            continue
                        seen.add(key)
                        result = parse_result(r)
                        if result:
                            yield result
                    log.info("adzuna_page", query=query, page=page, items=len(results), total=data.get("count"))
                    if len(results) < self.cfg.results_per_page:
                        break
        finally:
            if self._http is None:
                client.close()
