"""GitHub organisations located in India: technical startups that never post on job boards.

Needs GITHUB_TOKEN (a classic or fine-grained token with no scopes is enough) in .env; without it
the seeder is skipped. Search (GET /search/users?q=type:org location:"<city>" repos:>=N) returns at
most 1,000 results per query, so it runs one query per city. Each hit is looked up with
GET /orgs/<login> for its display name, website (`blog`), location and description; an org with
no website is skipped (open-source collectives, college clubs and personal projects mostly have
none). Org details are cached in data/cache/universe/github_orgs.json.

Rate limits with a token: search 30/min, REST 5,000/h. Search calls are spaced 2.2 s apart.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Callable, Iterator

from autoapply.discovery.universe import Seed, SeedContext, Seeder
from autoapply.logging import get_logger
from autoapply.sources.http_client import HttpClient
from autoapply.sources.location import find_cities

log = get_logger(__name__)

API = "https://api.github.com"
DEFAULT_CITIES = ["Bangalore", "Bengaluru", "Mumbai", "Pune", "Hyderabad", "Delhi", "Gurgaon", "Gurugram",
                  "Noida", "Chennai", "Kolkata", "Ahmedabad", "Jaipur", "Kochi", "India"]


class GitHubOrgSeeder(Seeder):
    name = "github"

    def __init__(self, http: HttpClient | None = None, sleep: Callable[[float], None] = time.sleep):
        self._http = http
        self._sleep = sleep

    @staticmethod
    def _token() -> str | None:
        from dotenv import load_dotenv
        from autoapply.config import PROJECT_ROOT
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        return os.environ.get("GITHUB_TOKEN")

    def available(self) -> str | None:
        return None if self._token() or self._http else "GITHUB_TOKEN not set in .env"

    def seeds(self, ctx: SeedContext) -> Iterator[Seed]:
        token = self._token()
        headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        client = self._http or HttpClient(requests_per_second=5.0, headers=headers)
        cache_path = ctx.cache_dir / "github_orgs.json"
        cache: dict[str, Any] = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        min_repos = int(ctx.options.get("min_repos", 3))
        max_orgs = int(ctx.options.get("max_orgs", 1500))
        logins: list[str] = []
        try:
            for city in ctx.options.get("cities") or DEFAULT_CITIES:
                for page in range(1, 11):
                    q = f'type:org location:"{city}" repos:>={min_repos}'
                    resp = client.get(f"{API}/search/users", params={"q": q, "per_page": 100, "page": page})
                    self._sleep(2.2)
                    if resp is None or resp.status_code != 200:
                        log.warning("github_search_failed", city=city, page=page, status=getattr(resp, "status_code", None))
                        break
                    items = (resp.json() or {}).get("items") or []
                    logins += [i["login"] for i in items if i.get("type") == "Organization"]
                    if len(items) < 100:
                        break
            logins = list(dict.fromkeys(logins))[:max_orgs]
            log.info("github_orgs_found", orgs=len(logins))
            for n, login in enumerate(logins, 1):
                org = cache.get(login)
                if org is None:
                    resp = client.get(f"{API}/orgs/{login}")
                    if resp is None or resp.status_code != 200:
                        continue
                    org = {k: resp.json().get(k) for k in ("login", "name", "blog", "location", "description",
                                                           "public_repos", "is_verified")}
                    cache[login] = org
                    if n % 100 == 0:
                        cache_path.write_text(json.dumps(cache))
                if not org.get("blog"):
                    continue
                yield Seed(name=org.get("name") or org["login"], website=org["blog"], is_india=True,
                           india_cities=list(find_cities(org.get("location") or "")),
                           about=org.get("description"))
        finally:
            cache_path.write_text(json.dumps(cache))
            if self._http is None:
                client.close()
