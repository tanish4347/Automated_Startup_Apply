"""Browser-backed source tier, for platforms that reject plain HTTP (Naukri, Wellfound, YC WaaS).

Approach: load the site's own page in a real Chromium, capture the XHR/fetch calls the page makes
(URL + request headers, including tokens the page's JS computes), then call the same JSON
endpoints with fetch() from inside the page, so cookies, origin and headers are the page's own.
DOM scraping is the fallback, not the plan.

Rules every browser source follows:
- One persistent profile per platform under data/browser_profiles/<platform>/, so cookies and
  a login made once with `autoapply browser-login <platform>` survive between runs.
- Every navigation and fetch goes through Budget.take() and Pacer.wait() (config/politeness.yaml).
- A CAPTCHA / bot challenge / block raises ChallengeDetected. Nothing here ever tries to solve or
  bypass one: the source stops, its run is marked `degraded` in source_runs, and discovery moves on.
- LinkedIn is refused outright (see sources/linkedin.py).
- Every XHR/fetch the pages make is appended (URL, method, status, request headers minus cookies and
  authorization) to data/browser_profiles/<platform>.calls.jsonl. That log is how a new internal
  endpoint gets reverse-engineered, e.g. after a first login.

Uses the full Chromium build (channel="chromium"), not the headless shell: Naukri's edge returns
403 "Access Denied" to the headless shell but serves the full build in headless mode.
"""

from __future__ import annotations

import abc
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from autoapply.config import DATA_DIR
from autoapply.logging import get_logger
from autoapply.politeness import Budget, BudgetExhausted, Pacer, SourcePolicy, policy_for
from autoapply.sources.base import BaseSource, SourceResult

log = get_logger(__name__)

PROFILE_ROOT = DATA_DIR / "browser_profiles"
USER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/125.0.0.0 Safari/537.36")
FORBIDDEN_PLATFORMS = {"linkedin"}
_REDACT_HEADERS = {"cookie", "authorization", "x-csrf-token", "csrf-token"}
_MAX_CALL_LOG_BYTES = 5_000_000


class ChallengeDetected(Exception):
    """A CAPTCHA, bot challenge or block page appeared. Never solved; the source degrades."""


def assert_platform_allowed(platform: str) -> None:
    if platform.lower() in FORBIDDEN_PLATFORMS or "linkedin" in platform.lower():
        raise AssertionError(f"browser automation of {platform!r} is forbidden (LinkedIn account safety)")


# ── Challenge detection ──────────────────────────────────────────────────────

_CHALLENGE_TITLES = ("access denied", "just a moment", "attention required", "are you a robot",
                     "verify you are human", "security check", "pardon our interruption", "captcha")
# Markup of an interactive challenge, not a CAPTCHA library merely being loaded: Naukri pulls in
# Google's recaptcha script and Wellfound Cloudflare's invisible Turnstile script
# (<script src="https://challenges.cloudflare.com/turnstile/v0/api.js">) on every normal page, so
# a script tag alone means nothing. A challenge shows as an iframe, a form or a cf-chl marker.
_CHALLENGE_HTML = re.compile(
    r'<iframe[^>]+challenges\.cloudflare\.com|id="challenge-form"|id="cf-challenge|cf-chl-widget'
    r'|<iframe[^>]+hcaptcha\.com|<iframe[^>]+captcha-delivery\.com|id="px-captcha"|class="g-recaptcha"'
    r'|<iframe[^>]+recaptcha/api2/bframe|title="recaptcha challenge', re.I)
_CHALLENGE_BODY = re.compile(r"recaptcha required|captcha required|access denied|are you a robot", re.I)


def detect_challenge(*, status: int | None = None, title: str = "", html: str = "",
                     body: str = "") -> str | None:
    """Reason string if this looks like a challenge/block, else None."""
    if status in (403, 429):
        return f"HTTP {status}"
    t = (title or "").strip().lower()
    if any(t.startswith(x) for x in _CHALLENGE_TITLES):
        return f"challenge page: {title.strip()[:60]!r}"
    m = _CHALLENGE_HTML.search(html or "")
    if m:
        return f"challenge markup: {m.group(0)}"
    if status in (401, 406) or _CHALLENGE_BODY.search((body or "")[:500]):
        m = _CHALLENGE_BODY.search((body or "")[:500])
        return f"HTTP {status}: {m.group(0) if m else (body or '')[:80]}"
    return None


# ── Session ──────────────────────────────────────────────────────────────────

_FETCH_JS = """async ({url, method, headers, body}) => {
    const r = await fetch(url, {method, headers, body, credentials: 'include'});
    return {status: r.status, text: await r.text()};
}"""


class BrowserSession:
    """One persistent Chromium context for one platform. Use as a context manager."""

    def __init__(self, platform: str, *, policy: SourcePolicy | None = None, budget: Budget | None = None,
                 headless: bool = True, profile_root: Path | None = None, record_calls: bool = True):
        assert_platform_allowed(platform)
        self.platform = platform
        self.policy = policy or policy_for(platform)
        self.budget = budget or Budget(platform, self.policy.daily_requests)
        self.pacer = Pacer(self.policy)
        self.headless = headless
        self.profile_dir = (profile_root or PROFILE_ROOT) / platform
        self.calls_path = (profile_root or PROFILE_ROOT) / f"{platform}.calls.jsonl"
        self.record_calls = record_calls
        self.requests_seen: list[tuple[str, dict[str, str]]] = []  # (url, headers) of xhr/fetch
        self._pw = self._ctx = self.page = None

    def __enter__(self) -> "BrowserSession":
        from playwright.sync_api import sync_playwright
        self.profile_dir.mkdir(parents=True, exist_ok=True)
        self._pw = sync_playwright().start()
        self._ctx = self._pw.chromium.launch_persistent_context(
            str(self.profile_dir), headless=self.headless, channel="chromium", user_agent=USER_AGENT,
            locale="en-IN", viewport={"width": 1366, "height": 900},
        )
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        self._ctx.on("request", self._on_request)
        self._ctx.on("response", self._on_response)
        return self

    def __exit__(self, *exc: Any) -> None:
        try:
            if self._ctx is not None:
                self._ctx.close()
        finally:
            if self._pw is not None:
                self._pw.stop()

    # recording
    def _on_request(self, request) -> None:
        if request.resource_type in ("xhr", "fetch"):
            self.requests_seen.append((request.url, dict(request.headers)))
            del self.requests_seen[:-200]

    def _on_response(self, response) -> None:
        req = response.request
        if not self.record_calls or req.resource_type not in ("xhr", "fetch"):
            return
        if "json" not in (response.headers.get("content-type") or ""):
            return
        try:
            if self.calls_path.exists() and self.calls_path.stat().st_size > _MAX_CALL_LOG_BYTES:
                return
            try:
                post = req.post_data
            except Exception:
                post = "<binary>"
            entry = {"at": datetime.now(timezone.utc).isoformat(), "method": req.method, "url": req.url,
                     "status": response.status,
                     "headers": {k: v for k, v in req.headers.items() if k.lower() not in _REDACT_HEADERS},
                     "post": (post or "")[:2000] or None}
            self.calls_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.calls_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:  # recording must never break a run
            log.debug("call_record_failed", error=str(e))

    def request_headers(self, url_part: str) -> dict[str, str] | None:
        """Headers of the most recent xhr/fetch the page itself sent to a URL containing url_part."""
        for url, headers in reversed(self.requests_seen):
            if url_part in url:
                return headers
        return None

    def read_only(self) -> None:
        """Abort every request that is not GET/HEAD/OPTIONS before it leaves the browser. Used when
        opening application forms: no click can submit anything, whatever the page does."""
        self.blocked: list[str] = []

        def guard(route) -> None:
            if route.request.method in ("GET", "HEAD", "OPTIONS"):
                route.continue_()
            else:
                self.blocked.append(f"{route.request.method} {route.request.url[:160]}")
                route.abort()

        self._ctx.route("**/*", guard)

    def check(self, status: int | None = None) -> None:
        reason = detect_challenge(status=status, title=self.page.title(), html=self.page.content())
        if reason:
            raise ChallengeDetected(f"{self.platform}: {reason} at {self.page.url}")

    # actions
    def goto(self, url: str, *, capture_request: str | None = None, capture_response: str | None = None,
             timeout_ms: int = 45000) -> Any:
        """Navigate like a person would (paced, budgeted). With capture_request, waits for the page's
        own xhr to that URL part and returns its headers; with capture_response, returns that
        response's JSON. Raises ChallengeDetected on a challenge page."""
        from playwright.sync_api import TimeoutError as PWTimeout
        self.budget.take()
        self.pacer.wait()
        try:
            if capture_request:
                with self.page.expect_request(lambda r: capture_request in r.url, timeout=timeout_ms) as info:
                    resp = self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                self.check(resp.status if resp else None)
                return dict(info.value.headers)
            if capture_response:
                with self.page.expect_response(lambda r: capture_response in r.url, timeout=timeout_ms) as info:
                    resp = self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                self.check(resp.status if resp else None)
                captured = info.value
                if detect_challenge(status=captured.status, body=captured.text()[:500]):
                    raise ChallengeDetected(f"{self.platform}: {captured.status} from {captured.url}")
                return captured.json()
            resp = self.page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
            self.page.wait_for_timeout(self.pacer._rng.uniform(1200, 2500))
            self.check(resp.status if resp else None)
            return resp.status if resp else None
        except PWTimeout:
            self.check()  # a challenge page is the usual reason the expected call never came
            raise

    def fetch_json(self, url: str, *, method: str = "GET", headers: dict[str, str] | None = None,
                   body: Any = None) -> Any:
        """Call a JSON endpoint from inside the page (same origin, same cookies)."""
        self.budget.take()
        self.pacer.wait()
        res = self.page.evaluate(_FETCH_JS, {"url": url, "method": method, "headers": headers or {},
                                             "body": json.dumps(body) if body is not None else None})
        reason = detect_challenge(status=res["status"], body=res["text"])
        if reason:
            raise ChallengeDetected(f"{self.platform}: {reason} from {url}")
        if res["status"] != 200:
            log.warning("browser_fetch_status", platform=self.platform, url=url, status=res["status"])
            return None
        return json.loads(res["text"])

    def content(self) -> str:
        return self.page.content()


# ── Source base ──────────────────────────────────────────────────────────────

class BrowserSource(BaseSource):
    """Subclasses set `platform` and implement harvest(session). discover() owns the session and
    turns a challenge into a degraded run instead of an exception."""

    tier = "browser"
    platform: str = ""

    def __init__(self, cfg: Any = None, *, headless: bool = True,
                 session_factory: Callable[["BrowserSource"], Any] | None = None):
        assert_platform_allowed(self.platform)
        self.cfg = cfg
        self.headless = headless
        self._session_factory = session_factory

    @property
    def name(self) -> str:
        return self.platform

    def _session(self):
        if self._session_factory is not None:
            return self._session_factory(self)
        return BrowserSession(self.platform, budget=getattr(self, "budget", None), headless=self.headless)

    @abc.abstractmethod
    def harvest(self, session: Any) -> Iterator[SourceResult]:
        ...

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        run = self.run_info
        try:
            with self._session() as session:
                yield from self.harvest(session)
        except ChallengeDetected as e:
            run.update(status="degraded", error=str(e))
            log.warning("browser_source_degraded", source=self.name, reason=str(e))
        except BudgetExhausted as e:
            run["error"] = str(e)
            log.info("browser_source_budget_exhausted", source=self.name)
