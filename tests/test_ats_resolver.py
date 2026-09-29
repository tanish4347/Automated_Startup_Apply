"""ATS resolver: evidence order, fingerprints, validated probes, resolution policy, and the
ats_boards source that only fetches resolved boards. No network: an httpx.MockTransport routes
every request, and anything unrouted is a 404."""

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables)
from autoapply.ats.harvesters import Target, parse_personio, parse_teamtailor
from autoapply.ats.http import AsyncFetcher
from autoapply.discovery.ats_resolver import (
    CompanyView, Resolution, apply_resolution, careers_links, due_companies, fingerprint, is_due, probe_slugs,
    resolve, run_resolver,
)
from autoapply.models.base import Base
from autoapply.models.company import Company
from autoapply.sources.ats_boards import AtsBoardsSource

FIXTURES = Path(__file__).parent / "fixtures" / "ats"
NOW = datetime(2026, 9, 30, tzinfo=timezone.utc)


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def routes(table: dict[str, tuple[int, str] | tuple[int, str, dict]]):
    """AsyncFetcher answering from {url: (status, body[, headers])}; unrouted -> 404."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url).split("?")[0]
        seen.append(url)
        hit = table.get(url)
        if hit is None:
            return httpx.Response(404, text="not found")
        status, body, *headers = hit
        return httpx.Response(status, text=body, headers=(headers[0] if headers else {"content-type": "text/html"}))

    return AsyncFetcher(max_retries=0, transport=httpx.MockTransport(handler)), seen


def run(coro):
    return asyncio.run(coro)


def resolve_with(table, company, **kw):
    async def go():
        f, seen = routes(table)
        async with f:
            return await resolve(f, company, **kw), seen
    return run(go())


# ── evidence, cheapest first ─────────────────────────────────────────────────

def test_own_job_urls_resolve_without_any_request():
    c = CompanyView(1, "Razorpay", website="https://razorpay.com",
                    job_urls=["https://www.naukri.com/job-listings-x", "https://job-boards.greenhouse.io/razorpaysoftwareprivatelimited/jobs/1"])
    r, seen = resolve_with({}, c)
    assert (r.ats_type, r.token, r.confidence) == ("greenhouse", "razorpaysoftwareprivatelimited", 0.9)
    assert seen == []


def test_homepage_careers_link_then_embedded_ats():
    home = '<nav><a href="/about">About</a><a href="/careers">We\'re hiring!</a></nav>'
    careers = '<h1>Join us</h1><iframe src="https://satsure.keka.com/careers/embed"></iframe>'
    r, seen = resolve_with({"https://satsure.com": (200, home), "https://satsure.com/careers": (200, careers)},
                           CompanyView(1, "SatSure", website="https://satsure.com"))
    assert (r.ats_type, r.token, r.careers_url) == ("keka", "satsure", "https://satsure.com/careers")
    assert r.confidence == 0.85 and "embeds" in r.evidence


def test_careers_redirect_onto_ats_host_is_strongest():
    table = {"https://acme.in": (200, "<p>home</p>"),
             "https://acme.in/careers": (302, "", {"location": "https://acme.freshteam.com/jobs"}),
             "https://acme.freshteam.com/jobs": (200, "<p>jobs</p>")}
    r, _ = resolve_with(table, CompanyView(1, "Acme", website="https://acme.in"))
    assert (r.ats_type, r.token, r.confidence) == ("freshteam", "acme", 0.95)


def test_unrecognised_careers_page_is_custom():
    table = {"https://tiny.in": (200, '<a href="/join-us">Careers</a>'),
             "https://tiny.in/join-us": (200, "<h2>Open positions</h2><ul><li>Backend intern</li></ul>")}
    r, _ = resolve_with(table, CompanyView(1, "Tiny", website="https://tiny.in"), allow_probe=False)
    assert (r.ats_type, r.careers_url, r.confidence) == ("custom", "https://tiny.in/join-us", 0.5)


def test_nothing_found():
    r, _ = resolve_with({}, CompanyView(1, "Ghost", website="https://ghost.example"), allow_probe=False)
    assert r.ats_type is None and r.evidence == "no careers page or ATS found"
    r, _ = resolve_with({}, CompanyView(2, "No Site Co"), allow_probe=False)
    assert r.evidence == "no website"


def test_fingerprint_prefers_the_companys_own_token():
    html = ('<a href="https://jobs.lever.co/partnerco">partner</a>'
            '<script src="https://boards.greenhouse.io/embed/job_board/js?for=acme"></script>')
    r = fingerprint("https://acme.com/careers", html, CompanyView(1, "Acme", website="https://acme.com"))
    assert (r.ats_type, r.token) == ("greenhouse", "acme")


def test_careers_links_filters_and_orders():
    html = ('<a href="/blog/jobs-report">Jobs report</a><a href="/careers">Careers</a>'
            '<a href="https://twitter.com/acme/jobs">x</a><a href="mailto:jobs@acme.com">mail</a>'
            '<a href="https://jobs.lever.co/acme">Open roles</a>')
    assert careers_links(html, "https://www.acme.com/") == [
        "https://jobs.lever.co/acme", "https://www.acme.com/careers", "https://www.acme.com/blog/jobs-report"]


# ── probes must be validated ─────────────────────────────────────────────────

def test_greenhouse_probe_requires_matching_board_name():
    board = '{"name": "Primer AI", "content": ""}'
    table = {"https://boards-api.greenhouse.io/v1/boards/primer": (200, board, {"content-type": "application/json"})}
    r, _ = resolve_with(table, CompanyView(1, "Primer", website="https://primer.io"))
    assert r.ats_type is None  # a board named "Primer AI" is someone else's


def test_smartrecruiters_probe_requires_jobs():
    empty = '{"totalFound": 0, "content": []}'
    url = "https://api.smartrecruiters.com/v1/companies/zzz/postings"
    r, _ = resolve_with({url: (200, empty, {"content-type": "application/json"})},
                        CompanyView(1, "Zzz", ats_token="zzz"))
    assert r.ats_type is None


def test_legacy_hint_is_verified_with_the_exact_token():
    url = "https://api.smartrecruiters.com/v1/companies/Wabtec/postings"
    table = {url: (200, '{"totalFound": 12}', {"content-type": "application/json"})}
    r, seen = resolve_with(table, CompanyView(1, "Wabtec", ats_type="smartrecruiters", ats_token="Wabtec"))
    assert (r.ats_type, r.token, r.confidence) == ("smartrecruiters", "Wabtec", 0.85)
    assert seen[0] == url  # the hint is tried before anything else


def test_probe_finds_keka_from_domain_slug():
    table = {"https://nutrabay.keka.com/careers/": (200, '<div>ats/documents/x/careerportal</div>')}
    r, _ = resolve_with(table, CompanyView(1, "Nutrabay", website="https://nutrabay.com"))
    assert (r.ats_type, r.token, r.confidence) == ("keka", "nutrabay", 0.75)


def test_probe_slugs():
    c = CompanyView(1, "Sarvam AI", website="https://www.sarvam.ai", ats_token="Sarvam")
    assert probe_slugs(c) == [("sarvam", 0.8), ("sarvamai", 0.6)]  # token and domain label agree: deduped
    assert probe_slugs(CompanyView(2, "Abc", website="https://abc-labs.in")) == [("abc-labs", 0.75)]


# ── policy ───────────────────────────────────────────────────────────────────

def _company(session, name, **kw):
    c = Company(name=name, normalized_name=name.lower(), **kw)
    session.add(c)
    session.flush()
    return c


def test_policy_recheck_backoff_and_generic_crawl(session):
    fresh = _company(session, "fresh", ats_resolved_at=NOW - timedelta(days=5))
    stale = _company(session, "stale", ats_resolved_at=NOW - timedelta(days=31))
    backing_off = _company(session, "backoff", ats_resolve_failures=2, ats_last_attempt_at=NOW - timedelta(days=3))
    backed_off = _company(session, "retry", ats_resolve_failures=2, ats_last_attempt_at=NOW - timedelta(days=4))
    dead = _company(session, "dead", ats_resolve_failures=4)
    never = _company(session, "never")
    assert [is_due(c, NOW) for c in (fresh, stale, backing_off, backed_off, dead, never)] == [
        False, True, False, True, False, True]
    assert {c.name for c in due_companies(session, 10, NOW)} == {"stale", "retry", "never"}

    c = _company(session, "failing", ats_resolve_failures=3)
    apply_resolution(c, Resolution(None, evidence="nothing"), NOW)
    assert (c.ats_resolve_failures, c.needs_generic_crawl, c.ats_last_attempt_at) == (4, True, NOW)

    apply_resolution(c, Resolution("keka", "x", "https://x.keka.com/careers/", 0.9, "e"), NOW)
    assert (c.ats_type, c.ats_resolve_failures, c.needs_generic_crawl, c.ats_resolved_at) == ("keka", 0, False, NOW)
    apply_resolution(c, Resolution("turbohire", "x", None, 0.95, "e"), NOW)
    assert c.needs_generic_crawl  # detected, but no verified jobs endpoint yet


def test_run_resolver_end_to_end(session, monkeypatch):
    from autoapply.discovery import ats_resolver
    _company(session, "Razorpay", website="https://razorpay.com", priority=2, is_india=True)
    _company(session, "Ghost", website="https://ghost.example")
    session.commit()

    async def fake_resolve_all(views, concurrency, allow_probe):
        return {v.id: (Resolution("greenhouse", "razorpay", "u", 0.95, "redirect") if v.name == "Razorpay"
                       else Resolution(None, evidence="nothing")) for v in views}

    monkeypatch.setattr(ats_resolver, "_resolve_all", fake_resolve_all)
    stats = run_resolver(session, limit=10)
    assert stats == {"attempted": 2, "greenhouse": 1, "failed": 1}
    rzp = session.query(Company).filter_by(name="Razorpay").one()
    assert rzp.ats_type == "greenhouse" and rzp.ats_resolved_at is not None
    assert run_resolver(session, limit=10) == {"attempted": 0}  # nothing due the same day


# ── fetchers for the newly supported ATSs ───────────────────────────────────

def test_personio_and_teamtailor_parsers():
    t = Target(company="Personio", ats_type="personio", token="personio")
    jobs = parse_personio((FIXTURES / "personio.xml").read_text(), t, "personio.jobs.personio.de")
    assert [j.title for j in jobs] == ["Staff Software Engineer, Data Platform", "Machine Learning Intern"]
    assert jobs[0].location == "Munich, Berlin" and jobs[0].source_url == "https://personio.jobs.personio.de/job/1834171"
    assert jobs[1].description_text.startswith("Your mission") and "NLP" in jobs[1].description_text

    tt = parse_teamtailor((FIXTURES / "teamtailor.rss").read_text(), Target("Teamtailor", "teamtailor", "career"))
    assert len(tt) == 2 and tt[0].work_mode == "hybrid" and tt[0].location == "London, United Kingdom"
    assert tt[0].source_url.startswith("https://career.teamtailor.com/jobs/") and len(tt[0].description_text) > 200


def test_ats_boards_only_fetches_resolved_boards(session):
    _company(session, "Resolved", ats_type="teamtailor", ats_token="career", ats_resolved_at=NOW)
    _company(session, "Unresolved", ats_type="greenhouse", ats_token="guess")        # never verified
    _company(session, "DetectOnly", ats_type="turbohire", ats_token="x", ats_resolved_at=NOW)  # no fetcher
    session.commit()
    source = AtsBoardsSource(session_factory=lambda: session)
    assert [t.company for _, t in source.targets(session)] == ["Resolved"]
