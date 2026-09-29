"""Directory seeders: YC (India), Inc42 Datalabs, Startup India.

Surveyed 2026-09-30:
- YC: the community mirror https://yc-oss.github.io/api/companies/all.json (6,260 companies,
  regenerated daily from YC's own directory) carries website, one_liner, regions, all_locations,
  status. ~220 list India among their regions.
- Inc42 Datalabs: https://inc42.com/inc42_datalabs.xml indexes 11 sitemaps of up to 25,000
  /company/<slug>/ pages each: Indian startups. Each page has a schema.org Corporation JSON-LD
  block (name, legalName, url = website, description "... operates in <X> industry ...").
  Far too many to fetch at once, so each run fetches up to max_pages new pages and remembers the
  ones it has done (data/cache/universe/inc42_done.txt): the directory fills in over several runs.
- Startup India (DPIIT directory): the public search API
  (POST api.startupindia.gov.in/sih/api/noauth/search/profiles) answered 503 on every attempt;
  the seeder tries once per run and reports it unreachable.
- Not implemented: Tracxn (paid; its public explore pages 404), NASSCOM 10,000 Startups (the
  directory page 404s).
"""

from __future__ import annotations

import html as htmllib
import json
import re
from typing import Any, Iterator

from autoapply.discovery.universe import Seed, SeedContext, Seeder
from autoapply.logging import get_logger
from autoapply.sources.http_client import HttpClient
from autoapply.sources.location import find_cities

log = get_logger(__name__)


class YCSeeder(Seeder):
    name = "yc"
    URL = "https://yc-oss.github.io/api/companies/all.json"

    def __init__(self, http: HttpClient | None = None):
        self._http = http

    @staticmethod
    def is_india(company: dict[str, Any]) -> bool:
        if "India" in (company.get("regions") or []):
            return True
        return bool(re.search(r"(^|;\s*|,\s*)India($|;)", company.get("all_locations") or ""))

    def seeds(self, ctx: SeedContext) -> Iterator[Seed]:
        statuses = set(ctx.options.get("statuses") or ["Active", "Public"])
        client = self._http or HttpClient(requests_per_second=1.0)
        try:
            resp = client.get(self.URL)
            if resp is None or resp.status_code != 200:
                log.warning("yc_fetch_failed", status=getattr(resp, "status_code", None))
                return
            companies = resp.json()
        finally:
            if self._http is None:
                client.close()
        for c in companies:
            if not self.is_india(c) or c.get("status") not in statuses:
                continue
            yield Seed(name=c["name"], website=c.get("website"), is_india=True,
                       india_cities=list(find_cities(c.get("all_locations") or "")),
                       about=f"YC {c.get('batch') or ''}: {c.get('one_liner') or ''}".strip(), high_priority=True)


_LDJSON = re.compile(r'<script type="application/ld\+json"[^>]*>(.*?)</script>', re.S)
_INDUSTRY = re.compile(r"operates in (?:the )?([A-Za-z][A-Za-z &/-]{1,40}?) (?:industry|sector|space)", re.I)


def parse_inc42_company(page: str) -> dict[str, Any] | None:
    for block in _LDJSON.findall(page):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        for node in data.get("@graph", [data]) if isinstance(data, dict) else data:
            if isinstance(node, dict) and node.get("@type") == "Corporation" and node.get("name"):
                desc = htmllib.unescape(node.get("description") or "")
                industry = _INDUSTRY.search(desc)
                address = node.get("address") or {}
                return {"name": htmllib.unescape(node["name"]), "legal_name": node.get("legalName"),
                        "website": node.get("url"), "description": desc,
                        "industry": industry.group(1).strip() if industry else None,
                        "city": address.get("addressLocality") if isinstance(address, dict) else None}
    return None


class Inc42Seeder(Seeder):
    name = "inc42"
    INDEX = "https://inc42.com/inc42_datalabs.xml"

    def __init__(self, http: HttpClient | None = None):
        self._http = http

    def _urls(self, client: HttpClient) -> list[str]:
        resp = client.get(self.INDEX)
        if resp is None or resp.status_code != 200:
            return []
        urls: list[str] = []
        for sitemap in re.findall(r"<loc>([^<]+)</loc>", resp.text):
            page = client.get(sitemap)
            if page is not None and page.status_code == 200:
                urls += [u for u in re.findall(r"<loc>([^<]+)</loc>", page.text) if "/company/" in u]
        return list(dict.fromkeys(urls))

    def seeds(self, ctx: SeedContext) -> Iterator[Seed]:
        max_pages = int(ctx.options.get("max_pages", 1500))
        industries = {i.casefold() for i in ctx.options.get("industries") or []}
        done_path = ctx.cache_dir / "inc42_done.txt"
        done = set(done_path.read_text(encoding="utf-8").split()) if done_path.exists() else set()
        client = self._http or HttpClient(requests_per_second=float(ctx.options.get("requests_per_second", 3.0)))
        fetched, kept = 0, 0
        try:
            urls = [u for u in self._urls(client) if u not in done]
            log.info("inc42_pending", pending=len(urls), done=len(done), this_run=min(max_pages, len(urls)))
            with open(done_path, "a", encoding="utf-8") as done_file:
                for url in urls[:max_pages]:
                    resp = client.get(url)
                    fetched += 1
                    if resp is None or resp.status_code != 200:
                        continue
                    done_file.write(url + "\n")
                    info = parse_inc42_company(resp.text)
                    if not info or (industries and (info["industry"] or "").casefold() not in industries):
                        continue
                    kept += 1
                    about = " ".join(x for x in (f"[{info['industry']}]" if info["industry"] else "",
                                                 info["description"][:600]) if x)
                    yield Seed(name=info["name"], website=info["website"], is_india=True,
                               india_cities=list(find_cities(info["city"] or "")), about=about or None)
        finally:
            if self._http is None:
                client.close()
            log.info("inc42_done", fetched=fetched, kept=kept)


class StartupIndiaSeeder(Seeder):
    name = "startup_india"
    API = "https://api.startupindia.gov.in/sih/api/noauth/search/profiles"

    def __init__(self, http: HttpClient | None = None):
        self._http = http

    def seeds(self, ctx: SeedContext) -> Iterator[Seed]:
        client = self._http or HttpClient(requests_per_second=1.0, max_retries=1,
                                          headers={"Origin": "https://www.startupindia.gov.in"})
        body = {"query": "", "focusSector": False, "industries": ctx.options.get("industries") or [],
                "sectors": [], "states": [], "cities": [], "stages": [], "badges": [], "roles": ["Startup"],
                "page": 0, "sort": {"orders": [{"field": "registeredOn", "direction": "DESC"}]},
                "dpiitRecogniseUser": True, "internationalUser": False}
        try:
            for page in range(int(ctx.options.get("max_pages", 50))):
                resp = client.post(self.API, json={**body, "page": page})
                if resp is None or resp.status_code != 200:
                    log.warning("startup_india_unreachable", status=getattr(resp, "status_code", None),
                                note="the public directory API has been returning 503; skipped")
                    return
                items = (resp.json() or {}).get("content") or []
                for item in items:
                    name = item.get("name") or item.get("companyName")
                    if name:
                        yield Seed(name=name, website=item.get("website"), is_india=True,
                                   india_cities=[c for c in [item.get("city")] if c])
                if not items:
                    return
        finally:
            if self._http is None:
                client.close()
