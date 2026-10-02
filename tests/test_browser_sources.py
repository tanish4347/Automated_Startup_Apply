"""Browser tier, politeness budgets and the LinkedIn guard. No browser, no network: browser sources
run against a FakeSession that replays recorded fixtures (tests/fixtures/<platform>/)."""

import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables)
from autoapply.appliers.harness import FormApplier
from autoapply.appliers.registry import find_applier, register_applier
from autoapply.config import NaukriSearch, WellfoundSearch, YcWaasSearch, load_search_config
from autoapply.models.base import Base
from autoapply.models.job import Job
from autoapply.models.source_run import SourceRun
from autoapply.politeness import (
    Budget, BudgetExhausted, Pacer, SourcePolicy, due, policy_for, requests_spent_today,
)
from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.browser import BrowserSession, BrowserSource, ChallengeDetected, detect_challenge
from autoapply.sources.http_client import HttpClient
from autoapply.sources.linkedin import (
    LinkedInAuthViolation, LinkedInGuestSource, assert_guest_request, assert_no_credentials_configured,
)
from autoapply.sources.naukri import NaukriSource, parse_job, search_url
from autoapply.sources.orchestrator import run_source
from autoapply.sources.wellfound import WellfoundSource, page_url, parse_page
from autoapply.sources.yc_waas import YcWaasSource, logged_in, page_props

FIXTURES = Path(__file__).parent / "fixtures"


def load(path: str) -> str:
    return (FIXTURES / path).read_text(encoding="utf-8")


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


class FakeSession:
    """Stands in for BrowserSession: goto()/fetch_json()/content() answered from a routes dict."""

    def __init__(self, goto=None, fetch=None):
        self._goto, self._fetch = goto or (lambda url, **kw: None), fetch or (lambda url, **kw: None)
        self.calls: list[tuple[str, str]] = []
        self.html = ""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def goto(self, url, **kw):
        self.calls.append(("goto", url))
        out = self._goto(url, **kw)
        if isinstance(out, str):  # an HTML page
            self.html = out
            return 200
        return out

    def fetch_json(self, url, **kw):
        self.calls.append(("fetch", url))
        return self._fetch(url, **kw)

    def content(self):
        return self.html


# ── Challenge detection ──────────────────────────────────────────────────────

def test_detect_challenge():
    assert detect_challenge(status=406, body=load("naukri/search_406.json")) == "HTTP 406: recaptcha required"
    assert detect_challenge(status=200, title="Access Denied") is not None
    assert detect_challenge(status=403) == "HTTP 403"
    assert detect_challenge(html='<iframe src="https://challenges.cloudflare.com/cdn-cgi/x"></iframe>')
    assert detect_challenge(html='<form id="challenge-form" action="/?__cf_chl_f_tk=x"></form>')
    assert detect_challenge(title="Just a moment...") is not None
    # A normal Naukri page loads Google's recaptcha script; that alone is not a challenge.
    normal = '<script src="https://www.google.com/recaptcha/api.js?render=explicit"></script><h1>Jobs</h1>'
    assert detect_challenge(status=200, title="Internship Jobs - Naukri.com", html=normal) is None
    assert detect_challenge(status=200, body='{"noOfJobs": 3}') is None
    # Wellfound loads Cloudflare's invisible Turnstile script on every normal page (seen live).
    turnstile = ('<script src="https://challenges.cloudflare.com/turnstile/v0/api.js?onload=turnstileLoad" '
                 'async="" defer=""></script><script id="__NEXT_DATA__">{}</script>')
    assert detect_challenge(status=200, title="Software Engineer Jobs in India - 2026 | Wellfound",
                            html=turnstile) is None
    assert detect_challenge(html='<div id="cf-chl-widget-abc"><iframe src="https://challenges.cloudflare.com/cdn-cgi/'
                                 'challenge-platform/h/b/turnstile/if/ov2"></iframe></div>')


def test_naukri_stipend_labels():
    from autoapply.sources.naukri import _pay_text
    assert _pay_text("25,000/month") == "INR 25,000 /month"
    assert _pay_text("10,000", internship=True) == "INR 10,000 /month"
    assert _pay_text("10,000") == "10,000" and _pay_text("Not disclosed") is None


def test_linkedin_is_never_a_browser_platform():
    with pytest.raises(AssertionError, match="forbidden"):
        BrowserSession("linkedin")

    class Sneaky(BrowserSource):
        platform = "linkedin_jobs"

        def harvest(self, session):
            yield from ()

    with pytest.raises(AssertionError, match="forbidden"):
        Sneaky()


# ── Naukri ───────────────────────────────────────────────────────────────────

BOOT_HEADERS = {"appid": "109", "systemid": "Naukri", "clientid": "d3skt0p", "nkparam": "tok==",
                "gid": "LOCATION,INDUSTRY,EDUCATION,FAREA_ROLE", "accept": "application/json",
                "content-type": "application/json", "cookie": "x=1", "user-agent": "UA"}


def _naukri_goto(url, capture_request=None, capture_response=None):
    if capture_request:
        return BOOT_HEADERS
    if capture_response and "070825502040" in url:
        return json.loads(load("naukri/detail_070825502040.json"))
    return None


def _naukri_fetch(url, headers=None, **kw):
    assert headers["nkparam"] == "tok==" and headers["appid"] == "109" and "cookie" not in headers
    return json.loads(load("naukri/search_page1.json")) if "pageNo=1" in url else {"jobDetails": []}


def test_naukri_search_details_and_cache(tmp_path):
    fake = FakeSession(_naukri_goto, _naukri_fetch)
    cfg = NaukriSearch(searches=[{"keyword": "machine learning intern", "experience": 0}], max_pages=3)
    source = NaukriSource(cfg, search_cfg=load_search_config(), cache_dir=tmp_path, session_factory=lambda s: fake)
    results = {r.source_id: r for r in source.discover()}

    assert source.run_info["status"] == "ok"
    fetches = [u for kind, u in fake.calls if kind == "fetch"]
    assert "experience=0" in fetches[0] and "keyword=machine+learning+intern" in fetches[0]
    assert len(results) == 3

    valeo = results["070825502040"]
    assert (valeo.title, valeo.company, valeo.employment_type) == ("Intern - AI", "Valeo", "Internship")
    assert valeo.application_url.startswith("https://valeo.wd3.myworkdayjobs.com/")  # direct ATS link
    assert valeo.source_url.startswith("https://www.naukri.com/job-listings-")
    assert valeo.description_text.startswith("Experience: 0-1 years")
    assert valeo.work_mode == "onsite" and valeo.location == "Chennai"
    assert valeo.company_meta["about"].startswith("As a technology company")
    assert (tmp_path / "070825502040.json").exists()

    # Second run reads the cached detail instead of opening the job page again.
    fake2 = FakeSession(_naukri_goto, _naukri_fetch)
    source2 = NaukriSource(cfg, search_cfg=load_search_config(), cache_dir=tmp_path, session_factory=lambda s: fake2)
    list(source2.discover())
    assert not any("070825502040" in u for kind, u in fake2.calls if kind == "goto")
    assert source2.stats["details_cached"] == 1


def test_naukri_challenge_degrades_instead_of_failing():
    def blocked(url, **kw):
        raise ChallengeDetected("naukri: HTTP 406: recaptcha required")

    fake = FakeSession(_naukri_goto, blocked)
    source = NaukriSource(NaukriSearch(fetch_details=False), session_factory=lambda s: fake)
    assert list(source.discover()) == []
    assert source.run_info["status"] == "degraded" and "recaptcha" in source.run_info["error"]
    # One header refresh, then it gives up: no retry loop against a challenge.
    assert [k for k, _ in fake.calls].count("fetch") == 2


def test_naukri_without_nkparam_degrades():
    fake = FakeSession(lambda url, **kw: {"appid": "109"}, _naukri_fetch)
    source = NaukriSource(NaukriSearch(), session_factory=lambda s: fake)
    assert list(source.discover()) == []
    assert source.run_info["status"] == "degraded"


def test_naukri_parse_and_urls():
    job = json.loads(load("naukri/search_page1.json"))["jobDetails"][1]
    r = parse_job(job)
    assert r.compensation_text == "Unpaid" and "Duration: 6 months duration" in r.description_text
    url = search_url({"keyword": "internship", "wfhType": 2, "cityTypeGid": 134}, 3)
    assert "wfhType=2" in url and "cityTypeGid=134" in url and "pageNo=3" in url and "noOfResults=20" in url


# ── Wellfound ────────────────────────────────────────────────────────────────

def test_wellfound_parses_apollo_state():
    results, pages = parse_page(load("wellfound/role_software-engineer_india.html"))
    assert pages == 20 and len(results) == 6
    first = results[0]
    assert (first.company, first.title, first.employment_type) == ("Shipthis Inc", "Software Engineer", "full-time")
    assert first.source_url == "https://wellfound.com/jobs/" + first.source_id + "-" + first.raw_data["slug"]
    assert first.compensation_text.startswith("₹7.2L")
    assert first.description_text.startswith("Experience: 1+ years")
    assert first.company_meta["is_india"] and "Bengaluru" in first.company_meta["india_cities"]
    remote = next(r for r in results if r.company == "Allminds")
    assert (remote.work_mode, remote.location) == ("remote", "Remote - India")
    assert page_url("software-engineer", "remote", 2) == "https://wellfound.com/role/r/software-engineer?page=2"
    assert page_url("data-scientist", "india", 1) == "https://wellfound.com/role/l/data-scientist/india"


def test_wellfound_harvest_pages_until_max():
    html = load("wellfound/role_software-engineer_india.html")
    fake = FakeSession(lambda url, **kw: html)
    source = WellfoundSource(WellfoundSearch(roles=["software-engineer"], locations=["india"], max_pages=2),
                             session_factory=lambda s: fake)
    results = list(source.discover())
    assert len(results) == 6  # page 2 repeats the same ids: yielded once
    assert [u for _, u in fake.calls] == ["https://wellfound.com/role/l/software-engineer/india",
                                          "https://wellfound.com/role/l/software-engineer/india?page=2"]


# ── YC Work at a Startup ─────────────────────────────────────────────────────

def test_yc_waas_reads_inertia_props():
    html = load("yc_waas/jobs_logged_out.html")
    props = page_props(html)
    assert not logged_in(props) and props["totalJobsCount"] == 2855
    fake = FakeSession(lambda url, **kw: html)
    results = list(YcWaasSource(YcWaasSearch(), session_factory=lambda s: fake).discover())
    intern = results[0]
    assert (intern.title, intern.employment_type, intern.source_url) == (
        "Software Engineering Intern", "Internship", f"https://www.workatastartup.com/jobs/{intern.source_id}")
    assert len(results) == 4 and all(r.company for r in results)


# ── Politeness ───────────────────────────────────────────────────────────────

def test_budget_counts_todays_runs(session):
    now = datetime.now(timezone.utc)
    session.add_all([
        SourceRun(source="linkedin", status="ok", requests=60, started_at=now),
        SourceRun(source="linkedin", status="ok", requests=35, started_at=now),
        SourceRun(source="linkedin", status="ok", requests=90, started_at=now - timedelta(days=1, hours=1)),
        SourceRun(source="naukri", status="ok", requests=500, started_at=now),
    ])
    session.commit()
    assert requests_spent_today(session, "linkedin") == 95
    budget = Budget.for_source(session, "linkedin")
    assert budget.limit == 100 and budget.remaining == 5
    for _ in range(5):
        budget.take()
    with pytest.raises(BudgetExhausted):
        budget.take()


def test_policies_and_cadence(session):
    assert policy_for("linkedin").daily_requests == 100
    assert policy_for("naukri").daily_requests >= 3000
    assert policy_for("some_new_source").daily_requests == 2000  # defaults
    naukri = policy_for("naukri")
    assert due(session, "naukri", naukri)
    session.add(SourceRun(source="naukri", tier="browser", status="ok", started_at=datetime.now(timezone.utc)))
    session.commit()
    assert not due(session, "naukri", naukri)
    assert due(session, "naukri", naukri, now=datetime.now(timezone.utc) + timedelta(hours=naukri.cadence_hours))


def test_pacer_randomises_and_long_pauses():
    slept = []
    pacer = Pacer(SourcePolicy(min_delay_s=2, max_delay_s=5, long_pause_every=3, long_pause_s=(60, 90)),
                  sleep=slept.append, rng=random.Random(7))
    delays = [pacer.wait() for _ in range(7)]
    assert delays[0] == 0  # the first request of a run
    assert all(2 <= d <= 5 for i, d in enumerate(delays[1:], 1) if i % 3)
    assert all(62 <= delays[i] <= 95 for i in (3, 6))  # every 3rd: base + long pause
    assert len(set(delays[1:])) == 6 and slept == delays[1:]


# ── Orchestrator: source_runs ────────────────────────────────────────────────

class _DegradingSource(BrowserSource):
    platform = "fakeplatform"

    def harvest(self, session):
        self.budget.take()
        yield SourceResult(title="ML Intern", company="Acme", source="fakeplatform", location="Remote")
        self.budget.take()
        raise ChallengeDetected("fakeplatform: HTTP 403")


def test_run_source_records_degraded_run(session):
    source = _DegradingSource(session_factory=lambda s: FakeSession())
    counts = run_source(session, source, load_search_config())
    run = session.query(SourceRun).one()
    assert (run.source, run.tier, run.status, run.requests, run.new) == ("fakeplatform", "browser", "degraded", 2, 1)
    assert "403" in run.error and run.finished_at is not None
    assert counts["new"] == 1 and session.query(Job).count() == 1


class _ViolatingSource(BaseSource):
    @property
    def name(self):
        return "linkedin"

    def discover(self, **kw):
        raise LinkedInAuthViolation("LinkedIn session cookie on a request: ['li_at']")
        yield


def test_linkedin_violation_is_recorded_and_reraised(session):
    with pytest.raises(LinkedInAuthViolation):
        run_source(session, _ViolatingSource(), load_search_config())
    run = session.query(SourceRun).one()
    assert run.status == "failed" and "li_at" in run.error


# ── LinkedIn guard ───────────────────────────────────────────────────────────

GUEST = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?keywords=ml"


def test_guard_allows_plain_guest_requests():
    assert_guest_request(httpx.Request("GET", GUEST, headers={"cookie": "bcookie=v=2; lang=v=2&lang=en-us"}))


@pytest.mark.parametrize("request_", [
    httpx.Request("GET", GUEST, headers={"cookie": "bcookie=1; li_at=AQEDAT"}),
    httpx.Request("GET", GUEST, headers={"authorization": "Bearer x"}),
    httpx.Request("POST", "https://www.linkedin.com/uas/login-submit", data={"session_key": "me"}),
    httpx.Request("GET", "https://www.linkedin.com/voyager/api/me", headers={"cookie": "JSESSIONID=a; li_rm=b"}),
])
def test_guard_fails_loudly_on_any_authentication(request_):
    with pytest.raises(LinkedInAuthViolation):
        assert_guest_request(request_)


def test_no_credentials_allowed_in_env(monkeypatch):
    monkeypatch.setenv("LINKEDIN_PASSWORD", "hunter2")
    with pytest.raises(LinkedInAuthViolation, match="credentials"):
        assert_no_credentials_configured()
    with pytest.raises(LinkedInAuthViolation):
        list(LinkedInGuestSource(["ml intern"], ["India"]).discover())


def _linkedin_client(route):
    from autoapply.sources.linkedin import assert_guest_request as hook
    seen = []

    def handler(request):
        seen.append(request)
        return route(request)

    client = HttpClient(requests_per_second=1000, max_retries=0, transport=httpx.MockTransport(handler),
                        event_hooks={"request": [hook]})
    return client, seen


def test_linkedin_honours_budget_and_paces(monkeypatch):
    for v in ("LINKEDIN_EMAIL", "LINKEDIN_USERNAME", "LINKEDIN_PASSWORD", "LINKEDIN_LI_AT", "LI_AT"):
        monkeypatch.delenv(v, raising=False)
    client, seen = _linkedin_client(lambda r: httpx.Response(200, text=load("linkedin/guest_search.html")))
    slept = []
    source = LinkedInGuestSource(["ml intern", "sde intern"], ["India", "Mumbai"], max_pages=2, http=client,
                                 rng=random.Random(1), sleep=slept.append)
    source.budget = Budget("linkedin", 100, spent_before=97)
    results = list(source.discover())

    assert len(seen) == 3  # 97 already spent today: only 3 more requests
    assert "daily budget" in source.run_info["error"]
    assert all(r.method == "GET" and r.url.path.startswith("/jobs-guest/") for r in seen)
    assert len(results) == 6 and results[0].title == "Machine Learning Intern"
    assert results[0].source_id == "machine-learning-intern-at-fixture-labs-4100000001"
    assert len(slept) == 2 and all(6 <= s <= 20 for s in slept)  # policy: 6-20 s between requests


def test_linkedin_stops_if_server_hands_out_a_session_cookie(monkeypatch):
    for v in ("LINKEDIN_EMAIL", "LINKEDIN_USERNAME", "LINKEDIN_PASSWORD", "LINKEDIN_LI_AT", "LI_AT"):
        monkeypatch.delenv(v, raising=False)
    client, seen = _linkedin_client(lambda r: httpx.Response(
        200, text=load("linkedin/guest_search.html"), headers={"set-cookie": "li_at=AQEDAT; Path=/; Domain=.linkedin.com"}))
    source = LinkedInGuestSource(["ml intern"], ["India"], max_pages=2, http=client, sleep=lambda s: None)
    source.budget = Budget("linkedin", 100)
    with pytest.raises(LinkedInAuthViolation, match="li_at"):
        list(source.discover())
    assert len(seen) == 1  # the second request, carrying li_at, never left


def test_linkedin_authwall_redirect_degrades(monkeypatch):
    for v in ("LINKEDIN_EMAIL", "LINKEDIN_USERNAME", "LINKEDIN_PASSWORD", "LINKEDIN_LI_AT", "LI_AT"):
        monkeypatch.delenv(v, raising=False)

    def route(r):
        if r.url.path.startswith("/jobs-guest/"):
            return httpx.Response(302, headers={"location": "https://www.linkedin.com/authwall?trk=x"})
        return httpx.Response(200, text="<html>Sign in</html>")

    client, _ = _linkedin_client(route)
    source = LinkedInGuestSource(["ml intern"], ["India"], http=client, sleep=lambda s: None)
    source.budget = Budget("linkedin", 100)
    assert list(source.discover()) == []
    assert source.run_info["status"] == "degraded" and "authwall" in source.run_info["error"]


# ── No LinkedIn applier, ever ────────────────────────────────────────────────

def test_linkedin_applier_cannot_be_registered():
    class LinkedInEasyApply(FormApplier):
        platform = "linkedin_easy_apply"

        def can_handle(self, job):
            return True

        def open_form(self, bs, job):
            raise NotImplementedError

        def submit_control(self, bs):
            raise NotImplementedError

        def success_assertion(self, bs, before_url):
            return None

    with pytest.raises(AssertionError, match="forbidden"):
        register_applier(LinkedInEasyApply())


def test_no_applier_for_linkedin_jobs():
    from autoapply.appliers.registry import setup_appliers
    setup_appliers()
    job = Job(title="ML Intern", source="linkedin", application_url="https://in.linkedin.com/jobs/view/123")
    assert find_applier(job) is None


def test_no_linkedin_applier_module_exists():
    appliers = Path(__file__).parent.parent / "autoapply" / "appliers"
    assert not [p.name for p in appliers.glob("*.py") if "linkedin" in p.name.lower()]
