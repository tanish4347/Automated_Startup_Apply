"""Resolve each company's ATS once, remember it, and re-check rarely.

Replaces the old career_pages source, which probed 5 US ATS endpoints per company on every run
and never remembered the answer. Resolution is its own command on its own cadence
(`autoapply ats resolve --limit N`); job fetching (sources/ats_boards.py) only ever calls
endpoints resolved here.

For one company, cheapest evidence first:
  0. its own jobs: an application/source URL on an ATS host resolves it with no request at all
  1. the careers page: known careers URL, else links on the homepage whose text or href says
     careers/jobs/hiring/join, else website + /careers, /jobs, /join-us, /work-with-us,
     /company/careers. A redirect onto an ATS host, or an ATS iframe/script/link in the page,
     identifies it (autoapply/ats/patterns.py).
  2. if still unknown: probe ATS APIs (Indian ATSs first) with slugs from the company's domain,
     its legacy board token, and its name. A probe counts only if the board exists; Greenhouse
     boards must also carry the company's name, SmartRecruiters boards must have jobs (it answers
     200 for any slug).
  3. a careers page with no recognisable ATS is recorded as ats_type=custom for the generic crawler.

Policy: a success is re-checked after 30 days. A failure backs off 2^failures days; after 4
failures the company is handed to the generic careers-page crawler (needs_generic_crawl).
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from sqlalchemy import or_
from sqlalchemy.orm import Session

from autoapply.ats.http import AsyncFetcher
from autoapply.ats.patterns import DETECT_ONLY, AtsRef, board_url, find_ats_refs
from autoapply.discovery.universe import domain_of
from autoapply.logging import get_logger
from autoapply.models.company import Company
from autoapply.services.dedup import normalize_company

log = get_logger(__name__)

RECHECK_AFTER = timedelta(days=30)
MAX_FAILURES = 4
CAREERS_PATHS = ("/careers", "/jobs", "/join-us", "/work-with-us", "/company/careers")
_CAREERS_LINK = re.compile(r"\bcareers?\b|\bjobs?\b|\bhiring\b|join[- ]?(?:us|our team|the team)|work[- ]with[- ]us"
                           r"|open (?:roles|positions)|we'?re hiring", re.I)
_JOBISH = re.compile(r"\b(open (?:positions|roles)|current openings|job openings|apply now|we'?re hiring"
                     r"|join (?:us|our team)|careers?|vacanc(?:y|ies)|internships?)\b", re.I)


@dataclass
class CompanyView:
    """What resolution needs, read from the DB up front so the async part never touches it."""
    id: int
    name: str
    website: str | None = None
    careers_url: str | None = None
    ats_type: str | None = None
    ats_token: str | None = None
    job_urls: list[str] = field(default_factory=list)


@dataclass
class Resolution:
    ats_type: str | None          # None = nothing found
    token: str | None = None
    careers_url: str | None = None
    confidence: float = 0.0
    evidence: str = ""


# ── fingerprinting ───────────────────────────────────────────────────────────

def _pick(refs: list[AtsRef], company: CompanyView) -> AtsRef | None:
    """Best ATS reference in a page: the one whose token looks like the company, else the most
    frequent type's first token. google_form alone is not an ATS."""
    refs = [r for r in refs if r.ats_type != "google_form"]
    if not refs:
        return None
    wanted = {normalize_company(company.name), (domain_of(company.website) or "").split(".")[0]}
    for r in refs:
        if normalize_company(r.token.split("|")[0].replace("-", " ")) in wanted:
            return r
    counts: dict[str, int] = {}
    for r in refs:
        counts[r.ats_type] = counts.get(r.ats_type, 0) + 1
    top = max(counts, key=counts.get)
    return next(r for r in refs if r.ats_type == top)


def careers_links(html: str, base: str) -> list[str]:
    """Links on a homepage that lead to careers: same site or an ATS host, text/href says so."""
    soup = BeautifulSoup(html, "lxml")
    site = urlsplit(base).netloc.lower().removeprefix("www.")
    out: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        label = f"{a.get_text(' ', strip=True)} {href}"
        if not _CAREERS_LINK.search(label):
            continue
        url = urljoin(base, href)
        host = urlsplit(url).netloc.lower().removeprefix("www.")
        if host.endswith(site) or site.endswith(host) or find_ats_refs(url):
            if url not in out:
                out.append(url)
    # An ATS link beats a same-site one; shorter paths (/careers) beat deep links.
    return sorted(out, key=lambda u: (not find_ats_refs(u), len(urlsplit(u).path)))[:3]


def fingerprint(final_url: str, html: str, company: CompanyView) -> Resolution | None:
    ref = _pick(find_ats_refs(final_url), company)
    if ref:
        return Resolution(ref.ats_type, ref.token, final_url, 0.95, f"redirect to {ref.evidence}")
    ref = _pick(find_ats_refs(html), company)
    if ref:
        return Resolution(ref.ats_type, ref.token, final_url, 0.85, f"page embeds {ref.evidence}")
    if re.search(r"teamtailor-cdn|assets\.teamtailor", html):
        host = urlsplit(final_url).netloc
        return Resolution("teamtailor", host, final_url, 0.8, "teamtailor assets on custom domain")
    return None


# ── probing ──────────────────────────────────────────────────────────────────

async def _json(f: AsyncFetcher, url: str, **kw: Any) -> Any:
    return await f.get_json(url, **kw)


async def _probe_greenhouse(f, slug, company):
    d = await _json(f, f"https://boards-api.greenhouse.io/v1/boards/{slug}")
    return isinstance(d, dict) and normalize_company(d.get("name")) == normalize_company(company.name)


async def _probe_lever(f, slug, company):
    return isinstance(await _json(f, f"https://api.lever.co/v0/postings/{slug}", params={"mode": "json", "limit": 1}), list)


async def _probe_ashby(f, slug, company):
    d = await _json(f, f"https://api.ashbyhq.com/posting-api/job-board/{slug}")
    return isinstance(d, dict) and "jobs" in d


async def _probe_smartrecruiters(f, slug, company):
    d = await _json(f, f"https://api.smartrecruiters.com/v1/companies/{slug}/postings", params={"limit": 1})
    return isinstance(d, dict) and (d.get("totalFound") or 0) > 0


async def _probe_recruitee(f, slug, company):
    d = await _json(f, f"https://{slug}.recruitee.com/api/offers/")
    return isinstance(d, dict) and "offers" in d


async def _probe_freshteam(f, slug, company):
    return isinstance(await _json(f, f"https://{slug}.freshteam.com/hire/widgets/jobs.json"), dict)


async def _probe_keka(f, slug, company):
    r = await f.get(f"https://{slug}.keka.com/careers/")
    return r is not None and r.status_code == 200 and "keka" in str(r.url) and "careerportal" in r.text


async def _probe_zoho(f, slug, company):
    for tld in ("in", "com"):
        r = await f.get(f"https://{slug}.zohorecruit.{tld}/jobs/Careers")
        if r is not None and r.status_code == 200 and 'id="jobs"' in r.text:
            return f"{slug}.zohorecruit.{tld}"
    return False


async def _probe_personio(f, slug, company):
    r = await f.get(f"https://{slug}.jobs.personio.de/xml")
    return r is not None and r.status_code == 200 and "<workzag-jobs" in r.text


async def _probe_teamtailor(f, slug, company):
    r = await f.get(f"https://{slug}.teamtailor.com/jobs.rss")
    return r is not None and r.status_code == 200 and "<rss" in r.text[:500]


async def _probe_breezy(f, slug, company):
    return isinstance(await _json(f, f"https://{slug}.breezy.hr/json"), list)


async def _probe_bamboohr(f, slug, company):
    d = await _json(f, f"https://{slug}.bamboohr.com/careers/list")  # unknown boards redirect to bamboohr.com
    return isinstance(d, dict) and "result" in d


Probe = Callable[[AsyncFetcher, str, CompanyView], Awaitable[Any]]
# Indian ATSs first.
PROBES: list[tuple[str, Probe]] = [
    ("keka", _probe_keka), ("zoho_recruit", _probe_zoho), ("freshteam", _probe_freshteam),
    ("greenhouse", _probe_greenhouse), ("lever", _probe_lever), ("ashby", _probe_ashby),
    ("recruitee", _probe_recruitee), ("smartrecruiters", _probe_smartrecruiters),
    ("personio", _probe_personio), ("teamtailor", _probe_teamtailor), ("breezy", _probe_breezy),
    ("bamboohr", _probe_bamboohr),
]


def probe_slugs(company: CompanyView, limit: int = 2) -> list[tuple[str, float]]:
    """(slug, confidence): the domain label is the most specific; a bare name is a guess."""
    out: list[tuple[str, float]] = []
    if company.ats_token and "|" not in company.ats_token and "." not in company.ats_token:
        out.append((company.ats_token.lower(), 0.8))
    domain = domain_of(company.website)
    if domain:
        out.append((domain.split(".")[0], 0.75))
    norm = normalize_company(company.name)
    if norm and len(norm) >= 5:
        out.append((norm, 0.6))
    seen, uniq = set(), []
    for slug, conf in out:
        if re.fullmatch(r"[a-z0-9][a-z0-9-]{1,60}", slug) and slug not in seen:
            seen.add(slug)
            uniq.append((slug, conf))
    return uniq[:limit]


async def probe(f: AsyncFetcher, company: CompanyView, max_slugs: int = 2) -> Resolution | None:
    # A legacy hint (career_pages.json: token + ATS) is checked first, token as written:
    # SmartRecruiters tokens are case-sensitive.
    hinted = dict(PROBES).get(company.ats_type or "")
    if hinted and company.ats_token:
        try:
            hit = await hinted(f, company.ats_token, company)
        except Exception:
            hit = False
        if hit:
            token = hit if isinstance(hit, str) else company.ats_token
            return Resolution(company.ats_type, token, board_url(company.ats_type, token), 0.85,
                              f"verified hint {company.ats_type}:{token}")
    for slug, conf in probe_slugs(company, max_slugs):
        for ats, fn in PROBES:
            try:
                hit = await fn(f, slug, company)
            except Exception:
                hit = False
            if hit:
                token = hit if isinstance(hit, str) else slug
                return Resolution(ats, token, board_url(ats, token), conf, f"probe {ats}:{token}")
    return None


# ── one company ──────────────────────────────────────────────────────────────

async def _page(f: AsyncFetcher, url: str) -> tuple[str, str] | None:
    resp = await f.get(url)
    if resp is None or resp.status_code != 200 or "html" not in resp.headers.get("content-type", "html"):
        return None
    return str(resp.url), resp.text


async def resolve(f: AsyncFetcher, company: CompanyView, *, allow_probe: bool = True) -> Resolution:
    # 0. our own jobs
    for url in company.job_urls:
        ref = _pick(find_ats_refs(url), company)
        if ref:
            return Resolution(ref.ats_type, ref.token, board_url(ref.ats_type, ref.token), 0.9, f"job url {ref.evidence}")

    # 1. careers page
    custom: str | None = None
    website = company.website
    if website and "//" not in website:
        website = "https://" + website
    candidates: list[str] = [company.careers_url] if company.careers_url else []
    if website:
        home = await _page(f, website)
        if home:
            hit = fingerprint(home[0], home[1], company)
            if hit and hit.confidence >= 0.95:
                return hit  # the "website" itself redirects to an ATS
            candidates += [u for u in careers_links(home[1], home[0]) if u not in candidates]
            if hit:  # the homepage embeds an ATS widget
                hit.careers_url = candidates[0] if candidates else home[0]
                return hit
        if len(candidates) < 2:
            candidates += [urljoin(website, p) for p in CAREERS_PATHS]
    for url in candidates[:4]:
        page = await _page(f, url)
        if page is None:
            continue
        hit = fingerprint(page[0], page[1], company)
        if hit:
            return hit
        if custom is None and _JOBISH.search(BeautifulSoup(page[1], "lxml").get_text(" ")[:20000] or ""):
            custom = page[0]

    # 2. probes
    if allow_probe:
        hit = await probe(f, company)
        if hit:
            return hit

    # 3. a careers page we can't identify
    if custom:
        return Resolution("custom", None, custom, 0.5, "careers page, no known ATS")
    return Resolution(None, evidence="no website" if not website else "no careers page or ATS found")


# ── policy + persistence ─────────────────────────────────────────────────────

def _aware(dt: datetime | None) -> datetime | None:
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


def is_due(c: Company, now: datetime) -> bool:
    if (c.ats_resolve_failures or 0) >= MAX_FAILURES:
        return False
    resolved = _aware(c.ats_resolved_at)
    if resolved is not None and now - resolved < RECHECK_AFTER:
        return False
    last = _aware(c.ats_last_attempt_at)
    if c.ats_resolve_failures and last is not None:
        return now >= last + timedelta(days=2 ** c.ats_resolve_failures)
    return True


def due_companies(session: Session, limit: int, now: datetime | None = None,
                  names: list[str] | None = None) -> list[Company]:
    now = now or datetime.now(timezone.utc)
    q = session.query(Company).filter(Company.ats_resolve_failures < MAX_FAILURES)
    if names:
        q = q.filter(or_(*[Company.name.ilike(n) for n in names]))
    q = q.order_by(Company.priority.desc(), Company.is_india.desc(), Company.website.is_(None), Company.id)
    out = []
    for c in q.yield_per(500):
        if names or is_due(c, now):
            out.append(c)
            if len(out) >= limit:
                break
    return out


def view_of(session: Session, c: Company) -> CompanyView:
    from autoapply.models.job import Job
    urls = [u for row in session.query(Job.application_url, Job.source_url).filter(Job.company_id == c.id).limit(50)
            for u in row if u]
    return CompanyView(c.id, c.name, c.website or (f"https://{c.domain}" if c.domain else None), c.careers_url,
                       c.ats_type, c.ats_token, urls)


def apply_resolution(c: Company, r: Resolution, now: datetime) -> None:
    c.ats_last_attempt_at = now
    c.ats_evidence = r.evidence
    if r.ats_type is None:
        c.ats_resolve_failures = (c.ats_resolve_failures or 0) + 1
        if c.ats_resolve_failures >= MAX_FAILURES:
            c.needs_generic_crawl = True
        return
    c.ats_type, c.ats_token = r.ats_type, r.token
    c.careers_url = r.careers_url or c.careers_url
    c.ats_confidence = r.confidence
    c.ats_resolved_at = now
    c.ats_resolve_failures = 0
    c.needs_generic_crawl = r.ats_type == "custom" or r.ats_type in DETECT_ONLY


async def _resolve_all(views: list[CompanyView], concurrency: int, allow_probe: bool) -> dict[int, Resolution]:
    sem = asyncio.Semaphore(concurrency)
    out: dict[int, Resolution] = {}

    async with AsyncFetcher(per_host=2, timeout=15.0, max_retries=1) as f:
        async def one(v: CompanyView) -> None:
            async with sem:
                try:
                    out[v.id] = await asyncio.wait_for(resolve(f, v, allow_probe=allow_probe), timeout=120)
                except Exception as e:  # one broken site never stops the batch
                    out[v.id] = Resolution(None, evidence=f"error: {type(e).__name__}: {str(e)[:120]}")
        await asyncio.gather(*(one(v) for v in views))
    return out


def run_resolver(session: Session, limit: int = 200, *, concurrency: int = 12, allow_probe: bool = True,
                 names: list[str] | None = None) -> dict[str, int]:
    now = datetime.now(timezone.utc)
    companies = due_companies(session, limit, now, names)
    views = [view_of(session, c) for c in companies]
    log.info("ats_resolve_started", companies=len(views))
    results = asyncio.run(_resolve_all(views, concurrency, allow_probe))
    stats: dict[str, int] = {"attempted": len(companies)}
    for c in companies:
        r = results.get(c.id) or Resolution(None, evidence="not resolved")
        apply_resolution(c, r, now)
        key = r.ats_type or "failed"
        stats[key] = stats.get(key, 0) + 1
        log.info("ats_resolved", company=c.name, ats=r.ats_type, token=r.token, confidence=r.confidence,
                 evidence=r.evidence)
    session.commit()
    return stats


def ats_stats(session: Session) -> dict[str, Any]:
    rows = session.query(Company.ats_type, Company.is_india, Company.ats_resolved_at, Company.ats_resolve_failures,
                         Company.needs_generic_crawl).all()
    by_platform: dict[str, list[int]] = {}
    for ats, india, resolved, failures, generic in rows:
        if resolved is None:
            continue
        slot = by_platform.setdefault(ats or "unknown", [0, 0])
        slot[0] += 1
        slot[1] += bool(india)
    return {
        "companies": len(rows),
        "resolved": sum(1 for r in rows if r[2] is not None),
        "resolved_india": sum(1 for r in rows if r[2] is not None and r[1]),
        "never_tried": sum(1 for r in rows if r[2] is None and not r[3]),
        "failing": sum(1 for r in rows if r[3] and r[3] < MAX_FAILURES),
        "generic_crawl": sum(1 for r in rows if r[4]),
        "by_platform": dict(sorted(by_platform.items(), key=lambda kv: -kv[1][0])),
    }
