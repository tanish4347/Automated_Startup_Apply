"""Resolve aggregator postings to where the application actually happens.

Aggregators (Himalayas, Unstop) list jobs whose "apply" leads somewhere else. Resolving follows
the posting to the real apply URL, then re-tags jobs.apply_channel and jobs.ats_platform from
the resolved host. A definitive resolution is cached on the job (resolved_apply_url,
apply_resolved_at, apply_resolve_note) and never redone. Blocked or failed attempts are noted but
not cached, so a later run can retry.

Per source (surveyed 2026-10-01):
  himalayas  posting pages (himalayas.app/companies/<co>/jobs/<slug>) sit behind a Cloudflare
             challenge, for plain HTTP and for the browser tier alike. The resolver opens them in
             a browser session; a challenge raises ChallengeDetected and is never solved. A one-
             time `autoapply browser-login himalayas` (solved by hand) may let later runs through.
  unstop     applications happen on Unstop's own registration form; posting pages carry no
             external apply link. Resolved without a request: apply_channel=unstop. (Unstop's
             robots.txt disallows /competitions/*/register and /api/* for crawlers.)
robots.txt is checked per host, over the same channel the page is fetched with; a disallowed or
unreadable robots.txt means no fetch. Requests go through the source's politeness budget.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urlsplit

from sqlalchemy.orm import Session

from autoapply.ats.patterns import find_ats_refs
from autoapply.logging import get_logger
from autoapply.models.job import Job
from autoapply.sources.filter import detect_apply_channel

log = get_logger(__name__)

AGGREGATORS = ("himalayas", "unstop")
USER_AGENT = "AutoApply"   # the agent name robots.txt rules are matched against


def channel_for(url: str | None, fallback_source: str | None = None) -> tuple[str, str | None]:
    """(apply_channel, ats_platform) for a resolved apply URL."""
    refs = [r for r in find_ats_refs(url or "") if r.ats_type != "google_form"]
    if refs:
        return "ats_direct", refs[0].ats_type
    return detect_apply_channel(fallback_source, None, url), None


def apply_channel_table(session: Session) -> Counter:
    q = session.query(Job.apply_channel).filter(Job.is_active == 1, Job.location_fit == "ok")
    return Counter(c or "unknown" for (c,) in q)


class RobotsRules:
    """robots.txt as crawlers apply it (RFC 9309), not as urllib.robotparser does: a blank line
    inside a group does not end it, `*` and `$` wildcards work, the longest matching rule wins and
    Allow wins a tie. (urllib.robotparser reads Internshala's file, whose `User-Agent: *` line is
    followed by a blank line, as allowing everything.)"""

    def __init__(self, text: str):
        self.groups: list[tuple[list[str], list[tuple[bool, str]]]] = []
        agents: list[str] = []
        rules: list[tuple[bool, str]] = []
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or ":" not in line:
                continue
            field, value = (x.strip() for x in line.split(":", 1))
            field = field.lower()
            if field == "user-agent":
                if rules:
                    self.groups.append((agents, rules))
                    agents, rules = [], []
                agents.append(value.lower())
            elif field in ("allow", "disallow") and agents:
                if value or field == "allow":
                    rules.append((field == "allow", value))
        if agents:
            self.groups.append((agents, rules))

    @staticmethod
    def _regex(pattern: str):
        import re
        body = re.escape(pattern).replace(r"\*", ".*")
        if body.endswith(r"\$"):
            body = body[:-2] + "$"
        return re.compile(body)

    def allowed(self, url: str, agent: str = USER_AGENT) -> bool:
        parts = urlsplit(url)
        path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
        agent = agent.lower()
        group = next((r for a, r in self.groups if any(x != "*" and x in agent for x in a)), None)
        if group is None:
            group = next((r for a, r in self.groups if "*" in a), [])
        best: tuple[int, bool] | None = None
        for allow, pattern in group:
            if pattern and self._regex(pattern).match(path):
                cand = (len(pattern), allow)
                if best is None or cand[0] > best[0] or (cand[0] == best[0] and allow):
                    best = cand
        return True if best is None else best[1]


class Robots:
    """robots.txt per host, fetched through `fetch_text` (the same channel as the pages)."""

    def __init__(self, fetch_text: Callable[[str], str | None]):
        self._fetch = fetch_text
        self._cache: dict[str, RobotsRules | None] = {}

    def allowed(self, url: str) -> bool | None:
        """True / False, or None when robots.txt could not be read (treated as not allowed)."""
        parts = urlsplit(url)
        host = f"{parts.scheme}://{parts.netloc}"
        if host not in self._cache:
            text = self._fetch(f"{host}/robots.txt")
            self._cache[host] = None if text is None or "<html" in text[:500].lower() else RobotsRules(text)
        rules = self._cache[host]
        return None if rules is None else rules.allowed(url)


def _record(job: Job, url: str | None, note: str, now: datetime, definitive: bool) -> None:
    job.apply_resolve_note = note
    if not definitive:
        return
    job.resolved_apply_url = url
    job.apply_resolved_at = now
    channel, platform = channel_for(url, job.source)
    job.apply_channel = channel
    if platform:
        job.ats_platform = platform


def reroute(job: Job, url: str, note: str, now: datetime | None = None) -> str:
    """An applier found that the posting applies elsewhere (Unstop's or Naukri's "apply on company
    site"). Cache the resolution and re-tag the job from the target URL, not from its source, so it
    leaves the platform's channel: an ATS host -> ats_direct + ats_platform, anything else -> company_site."""
    job.apply_resolve_note = note[:500]
    job.resolved_apply_url = url
    job.apply_resolved_at = now or datetime.now(timezone.utc)
    channel, platform = channel_for(url, None)
    job.apply_channel = "company_site" if channel == "unknown" else channel
    if platform:
        job.ats_platform = platform
    return job.apply_channel


def resolve_unstop(job: Job, now: datetime) -> str:
    _record(job, job.source_url or job.application_url,
            "applies on Unstop's own registration form; no external apply link on the posting", now, True)
    return "unstop"


def resolve_himalayas(job: Job, page_session, robots: Robots, now: datetime) -> str:
    """page_session: a BrowserSession-like object (goto, page, content). Never solves a challenge."""
    from autoapply.sources.browser import ChallengeDetected
    url = job.application_url or job.source_url
    allowed = robots.allowed(url)
    if allowed is not True:
        _record(job, None, "robots.txt " + ("disallows this page" if allowed is False else
                "unreadable (behind a challenge): not fetched"), now, False)
        return "robots"
    try:
        page_session.goto(url)
    except ChallengeDetected as e:
        _record(job, None, f"Cloudflare challenge, not solved ({e}). Run `autoapply browser-login himalayas` "
                           "to pass it by hand once, then resolve again.", now, False)
        return "challenge"
    links = page_session.page.evaluate("""() => [...document.querySelectorAll('a[href]')]
        .map(a => [a.innerText.trim(), a.href]).filter(([t, h]) => /apply/i.test(t) && !/himalayas\\.app/.test(h))""")
    target = links[0][1] if links else None
    if not target:
        _record(job, None, "posting page has no external apply link", now, False)
        return "no_link"
    _record(job, target, f"apply link on the posting: {target[:200]}", now, True)
    return "resolved"


def run_resolver(session: Session, sources: tuple[str, ...] = AGGREGATORS, limit: int = 500,
                 browser_factory: Callable[[], Any] | None = None) -> dict[str, Any]:
    """Resolve unresolved in-policy aggregator jobs. Returns before/after apply_channel counts
    and per-outcome counts."""
    from autoapply.politeness import Budget
    before = apply_channel_table(session)
    now = datetime.now(timezone.utc)
    jobs = (session.query(Job).filter(Job.source.in_(sources), Job.is_active == 1, Job.location_fit == "ok",
                                      Job.apply_resolved_at.is_(None))
            .order_by(Job.id).limit(limit).all())
    outcomes: Counter = Counter()
    for job in [j for j in jobs if j.source == "unstop"]:
        outcomes[resolve_unstop(job, now)] += 1
    session.commit()
    himalayas = [j for j in jobs if j.source == "himalayas"]
    if himalayas:
        from autoapply.sources.browser import BrowserSession

        def make():
            return BrowserSession("himalayas", budget=Budget.for_source(session, "himalayas"))
        with (browser_factory or make)() as bs:
            bs.read_only()

            def fetch_text(u: str) -> str | None:
                from autoapply.sources.browser import ChallengeDetected
                try:
                    bs.goto(u)
                except ChallengeDetected:
                    return None
                return bs.page.inner_text("body")
            robots = Robots(fetch_text)
            for job in himalayas:
                result = resolve_himalayas(job, bs, robots, now)
                outcomes[result] += 1
                session.commit()
                if result in ("challenge", "robots") and outcomes[result] >= 3:
                    # the same wall for every posting: stop spending budget on it
                    for rest in himalayas[himalayas.index(job) + 1:]:
                        rest.apply_resolve_note = job.apply_resolve_note
                        outcomes[result] += 1
                    session.commit()
                    break
    after = apply_channel_table(session)
    log.info("apply_resolver_done", **outcomes)
    return {"before": before, "after": after, "outcomes": dict(outcomes)}
