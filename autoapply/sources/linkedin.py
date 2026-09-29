"""LinkedIn job search: logged-out guest HTML only, under a strict policy.

Account safety is the constraint. The rules, enforced in code:
- Only GET requests to the public guest endpoint /jobs-guest/jobs/api/seeMoreJobPostings/search.
- Never authenticated. Every request passes assert_guest_request() as an httpx event hook; a
  request carrying an Authorization header or a LinkedIn session cookie (li_at, li_rm, liap, ...),
  or any non-GET request, raises LinkedInAuthViolation. The orchestrator re-raises it: a run
  stops loudly instead of logging and moving on.
- The source refuses to start if LinkedIn credentials are present in the environment.
- Never a browser source: BrowserSession/BrowserSource refuse the platform, and the applier
  registry refuses any LinkedIn applier. There is no LinkedIn applier, and there must never be one.
- At most daily_requests per UTC day (config/politeness.yaml, 100), summed across runs from
  source_runs; heavily randomised pauses between requests plus long pauses; query order shuffled.
- A 429, an empty response or a redirect off the guest endpoint (authwall/login) ends the run as
  `degraded`; nothing is retried.
"""

from __future__ import annotations

import os
import random
import time
import urllib.parse
from typing import Any, Callable, Iterator

import httpx
from bs4 import BeautifulSoup

from autoapply.logging import get_logger
from autoapply.politeness import Budget, BudgetExhausted, Pacer, policy_for
from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient

log = get_logger(__name__)

GUEST_PATH = "/jobs-guest/jobs/api/seeMoreJobPostings/search"
# LinkedIn's authenticated-session cookies. A guest never has any of them.
AUTH_COOKIES = ("li_at", "li_rm", "liap", "li_a", "li_ep_auth_context", "li_mc")
CREDENTIAL_ENV_VARS = ("LINKEDIN_EMAIL", "LINKEDIN_USERNAME", "LINKEDIN_PASSWORD", "LINKEDIN_LI_AT", "LI_AT")


class LinkedInAuthViolation(AssertionError):
    """Something tried to act as a signed-in LinkedIn user. Must never happen; never caught."""


def assert_guest_request(request: httpx.Request) -> None:
    if "linkedin.com" not in request.url.host:
        return
    if request.method != "GET":
        raise LinkedInAuthViolation(f"non-GET request to LinkedIn: {request.method} {request.url.path}")
    if request.headers.get("authorization"):
        raise LinkedInAuthViolation("Authorization header on a LinkedIn request")
    cookie = request.headers.get("cookie", "")
    names = {c.split("=", 1)[0].strip().lower() for c in cookie.split(";") if "=" in c}
    leaked = names.intersection(AUTH_COOKIES)
    if leaked:
        raise LinkedInAuthViolation(f"LinkedIn session cookie on a request: {sorted(leaked)}")


def assert_no_credentials_configured() -> None:
    present = [v for v in CREDENTIAL_ENV_VARS if os.environ.get(v)]
    if present:
        raise LinkedInAuthViolation(f"LinkedIn credentials found in the environment: {present}. "
                                    "Remove them; this system never signs in to LinkedIn.")


def parse_cards(html: str, keyword: str, fallback_location: str) -> list[SourceResult]:
    out = []
    for card in BeautifulSoup(html, "lxml").find_all("li"):
        title_elem = card.find("h3", class_="base-search-card__title")
        company_elem = card.find("h4", class_="base-search-card__subtitle")
        link_elem = card.find("a", class_="base-card__full-link")
        loc_elem = card.find("span", class_="job-search-card__location")
        if not title_elem or not link_elem:
            continue
        job_url = (link_elem.get("href") or "").split("?")[0]
        source_id = job_url.split("view/", 1)[1].strip("/") if "view/" in job_url else ""
        out.append(SourceResult(
            title=title_elem.get_text(strip=True),
            company=company_elem.get_text(strip=True) if company_elem else "",
            source="linkedin",
            source_id=source_id,
            source_url=job_url,
            application_url=job_url,
            location=loc_elem.get_text(strip=True) if loc_elem else fallback_location,
            raw_data={"keyword": keyword},
        ))
    return out


class LinkedInGuestSource(BaseSource):
    def __init__(self, keywords: list[str], locations: list[str], max_pages: int = 2,
                 http: HttpClient | None = None, rng: random.Random | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.keywords = keywords
        self.locations = locations
        self.max_pages = max_pages
        self._http = http
        self._rng = rng or random.Random()
        self._sleep = sleep

    @property
    def name(self) -> str:
        return "linkedin"

    def _client(self) -> HttpClient:
        # Pacing is the Pacer's job; one attempt per request, so the budget counts real traffic.
        return HttpClient(requests_per_second=100.0, max_retries=0,
                          event_hooks={"request": [assert_guest_request]})

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        assert_no_credentials_configured()
        policy = policy_for("linkedin")
        budget = self.budget or Budget("linkedin", policy.daily_requests)
        if self.budget is None:
            log.warning("linkedin_budget_not_from_db", note="run through the orchestrator to count today's usage")
        pacer = Pacer(policy, sleep=self._sleep, rng=self._rng)
        pairs = [(kw, loc) for kw in self.keywords for loc in self.locations]
        self._rng.shuffle(pairs)
        client = self._http or self._client()
        try:
            for kw, loc in pairs:
                for page in range(self.max_pages):
                    query = urllib.parse.urlencode({"keywords": kw, "location": loc, "f_TPR": "r604800",
                                                    "f_E": "1", "start": page * 25})
                    url = f"https://www.linkedin.com{GUEST_PATH}?{query}"
                    budget.take()
                    pacer.wait()
                    log.info("linkedin_fetching", keyword=kw, location=loc, start=page * 25,
                             budget_left=budget.remaining)
                    resp = client.get(url)
                    if resp is None or resp.status_code == 429:
                        self.run_info.update(status="degraded", error="rate limited or no response; stopped")
                        return
                    if not resp.url.path.startswith("/jobs-guest/"):
                        self.run_info.update(status="degraded", error=f"redirected off guest endpoint to {resp.url.path}")
                        return
                    if resp.status_code != 200 or not resp.text.strip():
                        break
                    cards = parse_cards(resp.text, kw, loc)
                    if not cards:
                        break
                    yield from cards
        except BudgetExhausted as e:
            self.run_info["error"] = str(e)
            log.info("linkedin_budget_exhausted", limit=budget.limit)
        finally:
            if self._http is None:
                client.close()
