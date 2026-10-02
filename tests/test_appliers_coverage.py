"""Prompt 16/17: the ATS appliers on the harness, Unstop and Naukri, rerouting, the merged registry
and coverage, multi-step forms, the company brief, and answer tier 3 (gate, verification, cache,
SENSITIVE refusal, never auto-submitted)."""

import json
import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401
from autoapply.appliers import registry, resolve
from autoapply.appliers.ashby import AshbyApplier, graphql_read_only
from autoapply.appliers.ats import PostingClosed
from autoapply.appliers.greenhouse import GreenhouseApplier
from autoapply.appliers.harness import Answer, FormApplier, FormField, NeedsLogin, Reroute, run_attempt
from autoapply.appliers.lever import LeverApplier
from autoapply.appliers.naukri import NaukriApplier
from autoapply.appliers.smartrecruiters import SmartRecruitersApplier
from autoapply.appliers.unstop import UnstopApplier
from autoapply.models.application import Application, ApplicationStatus
from autoapply.models.attempt import ApplicationClaim
from autoapply.models.base import Base
from autoapply.models.company import Company
from autoapply.models.generated_answer import GeneratedAnswer
from autoapply.models.job import Job
from autoapply.models.vault import VaultAnswer

CFG = {"daily_cap": 10, "submit_mode": {}, "evidence_dir": "data/evidence_test"}


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


# ── a scriptable fake page ───────────────────────────────────────────────────

class El:
    def __init__(self, page, sel, present=True, text="", attrs=None, on_click=None):
        self.page, self.sel, self.present, self.text, self.attrs, self.on_click = page, sel, present, text, attrs or {}, on_click

    first = property(lambda self: self)

    def count(self):
        return 1 if self.present else 0

    def filter(self, **kw):
        return self

    def or_(self, other):
        return self if self.present else other

    def inner_text(self):
        return self.text

    def get_attribute(self, name):
        return self.attrs.get(name)

    def click(self, **kw):
        self.page.clicks.append(self.sel)
        if self.on_click:
            self.on_click()


class Page:
    def __init__(self, url="https://x.test/", body="", els=None):
        self.url, self.body, self.els, self.clicks, self.filled = url, body, els or {}, [], {}
        self.keyboard = type("K", (), {"press": lambda *a: None})()

    def locator(self, sel):
        return self.els.get(sel) or El(self, sel, present=False)

    def get_by_role(self, role, name=None):
        for key, el in self.els.items():
            if key.startswith(f"role:{role}:") and name.search(key.split(":", 2)[2]):
                return el
        return El(self, f"role:{role}", present=False)

    def get_by_text(self, pattern):
        return self.els.get(f"text:{pattern.pattern}") or El(self, "text", present=False)

    def inner_text(self, sel):
        return self.body

    def wait_for_timeout(self, ms):
        pass

    def screenshot(self, path, full_page):
        Path(path).write_bytes(b"png")


class BS:
    def __init__(self, page, captured=None):
        self.page, self.captured, self.visited, self.blocked, self.read_only_on = page, captured, [], [], False

    def goto(self, url, **kw):
        self.visited.append(url)
        return self.captured if kw.get("capture_response") else 200

    def read_only(self, allow=None):
        self.read_only_on = True

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _app(session, **job_kw):
    job = Job(title=job_kw.pop("title", "ML Intern"), company=job_kw.pop("company", "Acme"),
              source=job_kw.pop("source", "naukri"), dedup_hash=str(len(session.query(Job).all())), **job_kw)
    session.add(job)
    session.flush()
    app = Application(job_id=job.id, company=job.company, role=job.title, status=ApplicationStatus.QUEUED)
    session.add(app)
    session.commit()
    return app


# ── ATS appliers: URLs, ownership, explicit success assertions ───────────────

def test_ats_apply_urls():
    gh = GreenhouseApplier()
    assert gh.apply_url(Job(application_url="https://stripe.com/jobs/search?gh_jid=6692166", company="Stripe",
                            ats_platform="greenhouse")) == "https://job-boards.greenhouse.io/embed/job_app?for=stripe&token=6692166"
    assert gh.apply_url(Job(application_url="https://boards.greenhouse.io/acme/jobs/123")).endswith("for=acme&token=123")
    assert LeverApplier().apply_url(Job(application_url="https://jobs.lever.co/acme/5546b440-6b61-4c36-8c3a-33a3df4af994")) \
        == "https://jobs.lever.co/acme/5546b440-6b61-4c36-8c3a-33a3df4af994/apply"
    assert AshbyApplier().apply_url(Job(application_url="https://jobs.ashbyhq.com/oneapp/84beb108-c04b-42d3-a9ae-9a91210201b7")) \
        == "https://jobs.ashbyhq.com/oneapp/84beb108-c04b-42d3-a9ae-9a91210201b7/application"
    with pytest.raises(LookupError):
        LeverApplier().apply_url(Job(application_url="https://jobs.lever.co/acme"))


def test_ats_ownership_by_platform_or_host():
    job = Job(apply_channel="ats_direct", resolved_apply_url="https://jobs.smartrecruiters.com/Acme/1")
    assert SmartRecruitersApplier().can_handle(job) and not LeverApplier().can_handle(job)
    assert GreenhouseApplier().can_handle(Job(ats_platform="greenhouse"))
    assert not GreenhouseApplier().can_handle(Job(ats_platform="greenhouse", application_url="https://linkedin.com/jobs/1"))


def test_greenhouse_success_is_explicit_and_never_the_asterisk_check():
    gh = GreenhouseApplier()
    form = "https://job-boards.greenhouse.io/embed/job_app?for=a&token=1"
    still_form = BS(Page(form, "First Name * Submit application", {"#application-form": El(None, "f")}))
    assert gh.success_assertion(still_form, form) is None
    done = BS(Page("https://job-boards.greenhouse.io/a/jobs/1/confirmation", ""))
    assert "confirmation" in gh.success_assertion(done, form)
    text_only = BS(Page(form, "Thank you for applying to Acme"))          # form gone + text
    assert "Thank you for applying" in gh.success_assertion(text_only, form)
    assert ".asterisk" not in Path(GreenhouseApplier.__module__.replace(".", "/") + ".py").read_text().split('"""', 2)[2]


def test_lever_and_ashby_success_assertions():
    assert LeverApplier().success_assertion(BS(Page("https://jobs.lever.co/a/b/thanks")), "x")
    assert LeverApplier().success_assertion(BS(Page("https://jobs.lever.co/a/b/apply", "Application submitted",
                                                     {"#application-form": El(None, "f")})), "x") is None
    shown = BS(Page(els={".ashby-application-form-success-container": El(None, "s")}))
    assert AshbyApplier().success_assertion(shown, "x")
    assert AshbyApplier().success_assertion(BS(Page(body="Apply now")), "x") is None


def test_ashby_read_only_allows_graphql_queries_never_mutations():
    url = "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting"
    assert graphql_read_only("POST", url, json.dumps({"query": "query ApiJobPosting { jobPosting { id } }"}))
    assert not graphql_read_only("POST", url, json.dumps({"query": "mutation ApiSubmitApplication { x }"}))
    assert not graphql_read_only("POST", "https://evil.test/api/non-user-graphql", json.dumps({"query": "query x"}))
    assert not graphql_read_only("POST", url, "<binary>")
    assert not graphql_read_only("PUT", url, json.dumps({"query": "query x"}))


def test_ats_closed_posting_raises_before_anything():
    page = Page(body="This job is no longer accepting applications")
    with pytest.raises(PostingClosed):
        LeverApplier().open_form(BS(page), Job(application_url="https://jobs.lever.co/a/5546b440-6b61-4c36-8c3a-33a3df4af994"))


def test_lever_tolerates_its_embedded_hcaptcha_but_not_other_challenges():
    from autoapply.sources.browser import ChallengeDetected

    class Walled(BS):
        def __init__(self, page, reason):
            super().__init__(page)
            self.reason = reason

        def goto(self, url, **kw):
            raise ChallengeDetected(self.reason)
    job = Job(application_url="https://jobs.lever.co/a/5546b440-6b61-4c36-8c3a-33a3df4af994")
    LeverApplier().open_form(Walled(Page(), "challenge markup: <iframe src=https://newassets.hcaptcha.com"), job)
    with pytest.raises(ChallengeDetected):
        LeverApplier().open_form(Walled(Page(), "challenge page: 'Just a moment...'"), job)
    with pytest.raises(ChallengeDetected):
        GreenhouseApplier().open_form(Walled(Page(), "hcaptcha"), Job(application_url="https://boards.greenhouse.io/a/jobs/1"))


# ── Naukri: the apply click is the submission ────────────────────────────────

def test_naukri_reroutes_company_site_jobs_without_a_request():
    bs = BS(Page())
    with pytest.raises(Reroute) as e:
        NaukriApplier().open_form(bs, Job(source_url="https://www.naukri.com/job-listings-x-1",
                                          application_url="https://acme.darwinbox.in/jobs/1"))
    assert e.value.url == "https://acme.darwinbox.in/jobs/1" and bs.visited == []


def test_naukri_reroutes_from_the_pages_own_job_json_and_detects_login():
    job = Job(source_url="https://www.naukri.com/job-listings-x-1", application_url="https://www.naukri.com/job-listings-x-1")
    with pytest.raises(Reroute):
        NaukriApplier().open_form(BS(Page(), {"jobDetails": {"applyRedirectUrl": "https://jobs.lever.co/a/b"}}), job)
    logged_out = Page(els={"#login_Layer:visible": El(None, "l"), "#apply-button:visible": El(None, "a")})
    with pytest.raises(NeedsLogin):
        NaukriApplier().open_form(BS(logged_out, {"jobDetails": {}}), job)
    ready = Page(els={"#apply-button:visible": El(None, "a")})
    NaukriApplier().open_form(BS(ready, {"jobDetails": {}}), job)   # no exception: the button is the submit


def test_naukri_review_only_parks_before_the_click(session, tmp_path):
    app = _app(session, source="naukri", apply_channel="naukri", source_url="https://www.naukri.com/job-listings-x-1",
               application_url="https://www.naukri.com/job-listings-x-1")
    page = Page("https://www.naukri.com/job-listings-x-1", els={"#apply-button:visible": El(None, "a"),
                                                                 "#apply-button": El(None, "#apply-button")})
    page.els["#apply-button"].page = page
    a = run_attempt(session, NaukriApplier(), app, lambda f, j: Answer(), browser_factory=lambda p: BS(page, {"jobDetails": {}}),
                    cfg={**CFG, "evidence_dir": str(tmp_path)}).attempt
    assert a.outcome == "parked" and "halted before the click" in a.park_reason and page.clicks == []
    assert app.status == ApplicationStatus.PARKED


def test_naukri_success_needs_applied_marker_and_no_chatbot():
    n = NaukriApplier()
    assert n.success_assertion(BS(Page(els={"#already-applied:visible": El(None, "x")})), "u")
    from autoapply.appliers.naukri import CHATBOT
    assert n.success_assertion(BS(Page(els={"#already-applied:visible": El(None, "x"), CHATBOT: El(None, "c")})), "u") is None
    assert n.success_assertion(BS(Page()), "u") is None


# ── Unstop ───────────────────────────────────────────────────────────────────

def _unstop_page(text="Quick Apply", cls="on_unstop oppid-1 register_btn", login=False):
    page = Page("https://unstop.com/internships/x-1")
    btn = El(page, "#un-register-btn", text=text, attrs={"class": cls},
             on_click=lambda: setattr(page, "url", "https://unstop.com/competitions/1/register"))
    page.els["#un-register-btn"] = btn
    if login:
        page.els[r"text:^\s*login\s*$"] = El(page, "login")
    return page


def test_unstop_external_and_closed_postings():
    job = Job(source_url="https://unstop.com/internships/x-1")
    with pytest.raises(Reroute) as e:
        UnstopApplier().open_form(BS(_unstop_page(), {"data": {"competition": {"regn_type": 2, "regn_url": "https://acme.com/apply"}}}), job)
    assert e.value.url == "https://acme.com/apply"
    with pytest.raises(PostingClosed):
        UnstopApplier().open_form(BS(_unstop_page("Application Closed"), {"data": {"competition": {"regn_type": 1}}}), job)
    with pytest.raises(NeedsLogin):
        UnstopApplier().open_form(BS(_unstop_page(login=True), {"data": {"competition": {"regn_type": 1}}}), job)
    page = _unstop_page()
    UnstopApplier().open_form(BS(page, {"data": {"competition": {"regn_type": 1, "regn_url": "https://acme.com"}}}), job)
    assert page.url.endswith("/register") and page.clicks == ["#un-register-btn"]


# ── harness: reroute, login wall, multi-step, tier 3 never auto-submits ──────

class StepApplier(FormApplier):
    platform, channels, max_steps = "unstop", ("unstop",), 3

    def __init__(self, pages, next_posts=False, label="Email"):
        self.pages, self.step, self.next_posts, self.label = pages, 0, next_posts, label

    def open_form(self, bs, job):
        pass

    def read_fields(self, bs):
        return [FormField(f"{self.label} {self.step}", "textarea" if self.label.startswith("Why") else "email", aa=str(self.step))]

    def fill(self, bs, field, value):
        bs.page.filled[field.label] = value

    def next_control(self, bs):
        if self.step + 1 >= self.pages:
            return None

        def go():
            self.step += 1
            if self.next_posts:
                bs.blocked.append("POST https://unstop.com/api/registration/step")
        return El(bs.page, "next", on_click=go)

    def submit_control(self, bs):
        return El(bs.page, "submit", on_click=lambda: setattr(bs.page, "url", "https://unstop.com/done/success"))

    def success_assertion(self, bs, before_url):
        return "done" if bs.page.url.endswith("/success") else None


def _go(session, tmp_path, applier, answer, mode="review_only"):
    app = _app(session, source="unstop", apply_channel="unstop")
    page = Page("https://unstop.com/competitions/1/register")
    a = run_attempt(session, applier, app, answer, browser_factory=lambda p: BS(page),
                    cfg={**CFG, "evidence_dir": str(tmp_path), "submit_mode": {"unstop": mode}}).attempt
    return app, a, page


def test_multi_step_form_fills_every_step(session, tmp_path):
    app, a, page = _go(session, tmp_path, StepApplier(3), lambda f, j: Answer("a@b.c", "email", "1a", 1.0))
    assert [f["label"] for f in a.filled_fields] == ["Email 0", "Email 1", "Email 2"] and page.clicks == ["next", "next"]
    assert a.outcome == "parked" and a.park_reason == "review_only: halted at the submit control"


def test_a_next_step_that_sends_data_halts_review_only(session, tmp_path):
    app, a, page = _go(session, tmp_path, StepApplier(3, next_posts=True), lambda f, j: Answer("a@b.c", "email", "1a", 1.0))
    assert a.outcome == "parked" and "step 1's Next tries to send data" in a.park_reason
    assert len(a.filled_fields) == 1


def test_reroute_retags_and_requeues(session, tmp_path):
    class Away(StepApplier):
        def open_form(self, bs, job):
            raise Reroute("https://jobs.lever.co/acme/1", "applies on Lever")
    app, a, _ = _go(session, tmp_path, Away(1), lambda f, j: Answer())
    assert a.outcome == "rerouted" and app.status == ApplicationStatus.QUEUED
    assert (app.job.apply_channel, app.job.ats_platform) == ("ats_direct", "lever")
    assert session.query(ApplicationClaim).count() == 0
    assert registry.setup_appliers() and isinstance(registry.find_applier(app.job), LeverApplier)


def test_login_wall_requeues_and_stops_the_platform(session, tmp_path):
    class Walled(StepApplier):
        def open_form(self, bs, job):
            raise NeedsLogin("not logged in")
    with pytest.raises(NeedsLogin):
        _go(session, tmp_path, Walled(1), lambda f, j: Answer())
    app = session.query(Application).one()
    assert app.status == ApplicationStatus.QUEUED and session.query(ApplicationClaim).count() == 0


def test_generated_answer_never_auto_submits_even_live(session, tmp_path):
    draft = Answer("I want to join because ...", "why_company", "3", 0.5, needs_approval=True, generated_id=1)
    app, a, page = _go(session, tmp_path, StepApplier(1, label="Why us?"), lambda f, j: draft, mode="live")
    assert a.outcome == "parked" and "awaiting your approval" in a.park_reason and "submit" not in page.clicks
    approved = Answer("I want to join because ...", "why_company", "3", 0.5, needs_approval=False, generated_id=1)
    app2 = _app(session, title="Other", company="Other Co", source="unstop", apply_channel="unstop")
    page2 = Page("https://unstop.com/competitions/2/register")
    a2 = run_attempt(session, StepApplier(1, label="Why us?"), app2, lambda f, j: approved, browser_factory=lambda p: BS(page2),
                     cfg={**CFG, "evidence_dir": str(tmp_path), "submit_mode": {"unstop": "live"}}).attempt
    assert a2.outcome == "submitted" and app2.answers_submitted[0]["tier"] == "3"


# ── registry, coverage, resolver ─────────────────────────────────────────────

def test_registry_has_every_applier_and_reports_coverage(session):
    names = [a.platform for a in registry.setup_appliers()]
    assert names == ["internshala", "unstop", "naukri", "greenhouse", "lever", "ashby", "smartrecruiters"]
    for i, (src, ch, url) in enumerate([("internshala", "internshala", None), ("unstop", "unstop", None),
                                        ("himalayas", "unknown", None), ("career_pages", "ats_direct", "https://jobs.lever.co/a/b"),
                                        ("linkedin", "linkedin_easy", "https://in.linkedin.com/jobs/view/1")]):
        session.add(Job(title=f"T{i}", company="C", source=src, apply_channel=ch, application_url=url, is_active=1,
                        location_fit="ok", dedup_hash=f"c{i}"))
    session.add(Job(title="out", company="C", source="internshala", apply_channel="internshala", is_active=1,
                    location_fit="outside_policy", dedup_hash="out"))
    session.commit()
    cov = registry.coverage(session)
    assert cov["total"] == 5 and cov["none"] == 2
    assert cov["by_applier"]["lever"] == 1 and cov["none_by_channel"] == {"unknown": 1, "linkedin_easy": 1}


def test_reroute_known_and_title_matching(session):
    job = Job(title="AI Intern", company="X", source="naukri", apply_channel="naukri", is_active=1, dedup_hash="n1",
              application_url="https://haire.ai/jobs/1")
    native = Job(title="AI Intern 2", company="X", source="naukri", apply_channel="naukri", is_active=1, dedup_hash="n2",
                 application_url="https://www.naukri.com/job-listings-x-2")
    session.add_all([job, native])
    session.commit()
    assert resolve.reroute_known(session) == 1
    assert job.apply_channel == "company_site" and native.apply_channel == "naukri"
    postings = [("Software Engineering Intern (Summer 2027)", "https://a/1"), ("Data Intern", "https://a/2")]
    assert resolve.match_posting("Software Engineering Intern (Summer 2027)", postings) == "https://a/1"
    assert resolve.match_posting("Backend Intern", postings) is None
    assert resolve.match_posting("Data Intern", postings + [("Data Intern", "https://a/3")]) is None   # ambiguous: never guessed


# ── company brief ────────────────────────────────────────────────────────────

HOME = """<html><head><title>Acme - payments for India</title><meta name="description" content="Acme builds UPI payment APIs for small merchants.">
<script type="application/ld+json">{"@type": "Organization", "foundingDate": "2019", "numberOfEmployees": {"value": 120},
"address": {"addressLocality": "Mumbai", "addressCountry": "IN"}}</script></head>
<body><p>We process over 2 million transactions every day for 40,000 merchants across India.</p><a href="/about-us">About us</a></body></html>"""
ABOUT = "<html><body><p>Acme was started by two engineers who wanted payments to just work for kirana stores.</p></body></html>"


def test_brief_is_extracted_once_and_respects_robots(session):
    c = Company(name="Acme", normalized_name="acme", website="https://acme.test", stage="Series A")
    session.add(c)
    session.commit()
    calls = []
    pages = {"https://acme.test/robots.txt": "User-agent: *\nDisallow: /private\n", "https://acme.test": HOME,
             "https://acme.test/about-us": ABOUT}

    def fetch(url):
        calls.append(url)
        return pages.get(url)
    from autoapply.services.company_brief import brief_for, passages, summary
    b = brief_for(session, c, fetch_text=fetch)
    s = summary(b)
    assert s["what"] == "Acme builds UPI payment APIs for small merchants." and s["size"] == "120"
    assert s["location"] == "Mumbai, IN" and s["stage"] == "Series A" and s["founded"] == "2019"
    assert s["sources"] == ["https://acme.test", "https://acme.test/about-us"]
    assert any("kirana" in p for p in passages(b))
    n = len(calls)
    assert brief_for(session, c, fetch_text=fetch) == b and len(calls) == n          # cached permanently
    blocked = Company(name="Shy", normalized_name="shy", website="https://shy.test")
    session.add(blocked)
    session.commit()
    b2 = brief_for(session, blocked, fetch_text=lambda u: "User-agent: *\nDisallow: /\n" if u.endswith("robots.txt") else HOME)
    assert b2["pages"] == [] and "disallows" in b2["fetch_error"]


# ── tier 3 ───────────────────────────────────────────────────────────────────

STORY = "I built a RAG search tool over 3000 legal documents using Python and FastAPI during my internship at Lexi."


@pytest.fixture
def story(session):
    from autoapply.candidate.sensitive import user_input
    with user_input():
        session.add_all([
            VaultAnswer(canonical_key="why_company", category="story", question="Why this company?", answer=STORY,
                        status="CONFIRMED", source="USER ENTERED"),
            VaultAnswer(canonical_key="additional_info", category="story", question="Anything else?",
                        answer="I enjoy shipping small tools that people use daily.", status="CONFIRMED", source="USER ENTERED"),
            VaultAnswer(canonical_key="gpa", category="education", question="CGPA", answer="9.12", status="CONFIRMED",
                        source="USER ENTERED", sensitivity="SENSITIVE"),
        ])
        c = Company(name="Acme", normalized_name="acme", company_brief={
            "db": {"name": "Acme"}, "pages": [{"url": "https://acme.test", "description": "Acme builds UPI payment APIs.",
                                                "paragraphs": ["Acme serves 40,000 merchants."]}]})
        session.add(c)
        session.commit()
    return session


class FakeGen:
    model = "fake"

    def __init__(self, draft, claims):
        self.d, self.c, self.calls = draft, claims, 0

    def available(self):
        return True

    def draft(self, prompt):
        self.calls += 1
        self.prompt = prompt
        return self.d

    def claims(self, prompt):
        return self.c


def _job(session):
    c = session.query(Company).filter_by(normalized_name="acme").one()
    j = Job(title="ML Intern", company="Acme", company_id=c.id, source="internshala", dedup_hash="j")
    session.add(j)
    session.commit()
    return j


GOOD = "I built a RAG search tool over 3000 legal documents using Python and FastAPI. Acme builds UPI payment APIs."
GOOD_CLAIMS = [{"claim": "built a RAG tool", "source": "S1", "quote": "I built a RAG search tool over 3000 legal documents"},
               {"claim": "Acme builds UPI APIs", "source": "B2", "quote": "Acme builds UPI payment APIs"}]


def test_tier3_is_disabled_until_the_story_pass_is_complete(session):
    from autoapply.answers.generate import generate_answer, tier3_status
    from autoapply.candidate.catalog import seed_catalog
    seed_catalog(session)
    on, why = tier3_status(session)
    assert not on and "disabled until the story pass is complete" in why and "0 of 3" in why
    gen = FakeGen(GOOD, GOOD_CLAIMS)
    j = Job(title="ML Intern", company="Acme", source="internshala", dedup_hash="j")
    g = generate_answer(session, "Why do you want to join us?", "why_company", j, gen, {})
    assert g.value is None and "disabled" in g.park_reason and gen.calls == 0


def test_tier3_verified_draft_is_pending_and_cached(story):
    from autoapply.answers.generate import generate_answer
    gen = FakeGen(GOOD, GOOD_CLAIMS)
    j = _job(story)
    g = generate_answer(story, "Why do you want to join us?", "why_company", j, gen, {})
    assert (g.value, g.status) == (GOOD, "pending") and gen.calls == 1
    assert "9.12" not in gen.prompt and "[S1]" in gen.prompt and "[B1]" in gen.prompt
    again = generate_answer(story, "Why do you want to join us?", "why_company", j, gen, {})
    assert again.value == GOOD and gen.calls == 1                                    # never regenerated


def test_tier3_rejects_unsupported_claims_and_does_not_retry(story):
    from autoapply.answers.generate import generate_answer
    bad = "I led a team of 12 engineers at Google building Kubernetes operators."
    gen = FakeGen(bad, [{"claim": "led a team at Google", "source": None, "quote": None}])
    j = _job(story)
    g = generate_answer(story, "Tell us about a project", "custom.project", j, gen, {})
    assert g.value is None and g.status == "rejected" and "verification" in g.park_reason
    assert any("Google" in r for r in g.reasons) and any("12" in r for r in g.reasons)
    g2 = generate_answer(story, "Tell us about a project", "custom.project", j, gen, {})
    assert g2.value is None and gen.calls == 1                                       # parked, not retried vaguer
    row = story.query(GeneratedAnswer).one()
    assert row.status == "rejected" and row.verification["reasons"]


def test_verification_checks_quotes_numbers_and_names():
    from autoapply.answers.generate import check
    ctx = [{"id": "S1", "source": "story", "text": STORY}]
    assert check("I built a RAG search tool.", [{"claim": "x", "source": "S1", "quote": "built a RAG search tool"}], ctx, "") == []
    assert check("I built a RAG search tool.", [{"claim": "x", "source": "S1", "quote": "built a vector database"}], ctx, "")
    assert check("I built it with 5000 documents.", [{"claim": "x", "source": "S1", "quote": "I built"}], ctx, "")
    assert check("I built it in Rust.", [{"claim": "x", "source": "S1", "quote": "I built"}], ctx, "")
    assert check("A tool.", None, ctx, "")


def test_tier3_never_touches_sensitive_fields(story):
    from autoapply.answers.engine import AnswerEngine
    from autoapply.answers.entail import SensitiveLeak
    from autoapply.answers.generate import generate_answer
    from autoapply.candidate.sensitive import SENSITIVE_KEYS, ParkApplication
    gen = FakeGen(GOOD, GOOD_CLAIMS)
    j = _job(story)
    for key in sorted(SENSITIVE_KEYS) + ["custom.criminal_record_explain"]:
        with pytest.raises(SensitiveLeak):
            generate_answer(story, "Please explain", key, j, gen, {})
    eng = AnswerEngine(story, generator=gen, entailer=type("E", (), {"available": lambda s: False})(), writeback=False)
    with pytest.raises(ParkApplication):   # SENSITIVE + no confirmed answer parks at tier 1; the model never runs
        eng.resolve("Please describe your expected stipend and why", "textarea", None, job=j)
    assert gen.calls == 0
    # a story answer that contains a SENSITIVE value can't reach a prompt
    from autoapply.candidate.sensitive import user_input
    with user_input():
        story.query(VaultAnswer).filter_by(canonical_key="additional_info").one().answer = "My CGPA is 9.12."
        story.commit()
    with pytest.raises(SensitiveLeak):
        generate_answer(story, "Why do you want to join us?", "custom.why_join", j, gen, {})


def test_engine_uses_tier3_for_open_questions_and_review_approves(story, tmp_path):
    from autoapply.answers.engine import AnswerEngine
    from autoapply.appliers import review
    gen = FakeGen(GOOD, GOOD_CLAIMS)
    j = _job(story)
    eng = AnswerEngine(story, generator=gen, entailer=type("E", (), {"available": lambda s: False})())
    r = eng.resolve("Why do you want to join Acme?", "textarea", None, job=j)
    assert (r.tier, r.value, r.needs_approval) == ("3", GOOD, True)
    assert eng.resolve("Why do you want to join Acme?", "textarea", None).value != GOOD   # no job: no tier 3
    app = _app(story, source="unstop", apply_channel="unstop", title="Data Intern")
    page = Page("https://unstop.com/competitions/3/register")
    answer = lambda f, job: Answer(r.value, r.canonical_key, "3", 0.5, needs_approval=True, generated_id=r.generated_id)  # noqa: E731
    a = run_attempt(story, StepApplier(1, label="Why us?"), app, answer, browser_factory=lambda p: BS(page),
                    cfg={**CFG, "evidence_dir": str(tmp_path), "submit_mode": {"unstop": "live"}}).attempt
    assert a.outcome == "parked"
    review.decide(story, a.id, "edit", {a.filled_fields[0]["label"]: "My own words about Acme."})
    row = story.get(GeneratedAnswer, r.generated_id)
    assert row.status == "approved" and row.draft == "My own words about Acme."
