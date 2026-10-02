"""Application infrastructure: stall reset, dedup-cluster claims, daily caps, submit_mode, the
attempt runner's outcomes and evidence, the apply-link resolver, and /review decisions."""

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401
from autoapply.appliers import harness, resolve, review
from autoapply.appliers.harness import (
    Answer, CapReached, FormApplier, FormField, attempts_today, check_caps, claim, cluster_key, run_attempt,
    submit_mode,
)
from autoapply.models.application import Application, ApplicationStatus
from autoapply.models.attempt import ApplicationAttempt, ApplicationClaim
from autoapply.models.base import Base
from autoapply.models.job import Job
from autoapply.services.application_service import reset_stalled_applications

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
CFG = {"daily_cap": 3, "platform_caps": {"internshala": 2}, "submit_mode": {}, "evidence_dir": "data/evidence_test"}


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _app(session, title="Data Science Intern", company="Acme Labs Pvt Ltd", source="internshala", status=ApplicationStatus.QUEUED, **kw):
    job = Job(title=title, company=company, source=source, apply_channel=source, source_url=f"https://{source}.com/j/{title}",
              dedup_hash=f"{source}-{title}-{company}-{kw.pop('n', 0)}")
    session.add(job)
    session.flush()
    app = Application(job_id=job.id, company=company, role=title, status=status, **kw)
    session.add(app)
    session.commit()
    return app


# ── 1. stall reset ───────────────────────────────────────────────────────────

def test_reset_stalled_applications(session):
    old = NOW - timedelta(hours=2)
    a = _app(session, status=ApplicationStatus.IN_PROGRESS, n=1)
    b = _app(session, title="ML Intern", status=ApplicationStatus.IN_PROGRESS, retry_count=2, max_retries=3, n=2)
    fresh = _app(session, title="SDE Intern", status=ApplicationStatus.IN_PROGRESS, n=3)
    for app in (a, b):
        app.updated_at = old
    fresh.updated_at = NOW - timedelta(minutes=5)
    session.commit()
    assert reset_stalled_applications(session, timeout_minutes=30, now=NOW) == 2
    assert (a.status, a.retry_count) == (ApplicationStatus.QUEUED, 1)
    assert (b.status, b.retry_count) == (ApplicationStatus.FAILED, 3)   # past max_retries
    assert fresh.status == ApplicationStatus.IN_PROGRESS


def test_apply_engine_imports():
    import autoapply.appliers.orchestrator  # noqa: F401  (AUDIT 1.9: this used to fail)


# ── 6. never double-apply ────────────────────────────────────────────────────

def test_same_internship_on_two_sources_is_one_cluster(session):
    a = _app(session, company="Acme Labs Pvt. Ltd.", source="internshala", n=1)
    b = _app(session, company="ACME LABS", source="unstop", n=2)
    assert cluster_key(a.job) == cluster_key(b.job)


def test_second_attempt_in_a_cluster_is_refused(session, tmp_path):
    first = _app(session, company="Acme Labs Pvt. Ltd.", source="internshala", n=1)
    second = _app(session, company="ACME LABS", source="unstop", n=2)
    assert run_attempt(session, FakeApplier(), first, answer_all, browser_factory=FakeBrowser.factory(),
                       cfg={**CFG, "evidence_dir": str(tmp_path)}, now=NOW).attempt is not None
    refused = run_attempt(session, FakeApplier("unstop"), second, answer_all, browser_factory=FakeBrowser.factory(),
                          cfg={**CFG, "evidence_dir": str(tmp_path)}, now=NOW)
    assert refused.attempt is None and "already claimed" in refused.refused
    assert second.status == ApplicationStatus.CLOSED
    assert session.query(ApplicationAttempt).count() == 1


def test_claims_are_unique_in_the_database(session):
    a, b = _app(session, n=1), _app(session, source="unstop", n=2)
    session.add_all([ApplicationClaim(dedup_cluster="x|y", application_id=a.id),
                     ApplicationClaim(dedup_cluster="x|y", application_id=b.id)])
    with pytest.raises(IntegrityError):
        session.commit()


# ── 5. volume policy ─────────────────────────────────────────────────────────

def test_daily_caps_count_from_the_applications_table(session, tmp_path):
    for i in range(2):
        _app(session, title=f"Intern {i}", n=10 + i, last_attempt_at=NOW - timedelta(hours=1))
    _app(session, title="Yesterday", n=20, last_attempt_at=NOW - timedelta(days=1))
    assert attempts_today(session, now=NOW) == 2 and attempts_today(session, "internshala", now=NOW) == 2
    with pytest.raises(CapReached, match="internshala"):
        check_caps(session, "internshala", CFG, NOW)       # per-platform sub-cap (2)
    check_caps(session, "unstop", CFG, NOW)                 # global 3 not reached yet
    _app(session, title="Unstop one", source="unstop", n=30, last_attempt_at=NOW)
    with pytest.raises(CapReached, match="daily cap of 3"):
        check_caps(session, "unstop", CFG, NOW)
    # a "restart" (new session on the same database) sees the same count
    assert attempts_today(sessionmaker(bind=session.get_bind())(), now=NOW) == 3


def test_pacing_is_jittered():
    import random
    slept = []
    delays = [harness.pause({"pacing": {"min_s": 10, "max_s": 20, "long_pause_every": 3, "long_pause_s": [100, 200]}},
                            n, slept.append, random.Random(n)) for n in range(1, 7)]
    assert all(10 <= d <= 20 for n, d in enumerate(delays, 1) if n % 3)
    assert all(110 <= delays[n - 1] <= 220 for n in (3, 6)) and len(set(delays)) == 6


# ── 3. submit_mode: review_only unless config says live ──────────────────────

def test_submit_mode_defaults_to_review_only():
    assert submit_mode("internshala", {}) == "review_only"
    assert submit_mode("internshala", {"submit_mode": {"internshala": "Live"}}) == "review_only"
    assert submit_mode("internshala", {"submit_mode": {"internshala": True}}) == "review_only"
    assert submit_mode("internshala", {"submit_mode": {"internshala": "live"}}) == "live"
    real = harness.load_config()
    assert all(v == "review_only" for v in (real.get("submit_mode") or {}).values())


def test_no_code_can_promote_submit_mode():
    """config/apply.yaml is only ever read: no module writes it, or sets submit_mode."""
    root = Path(harness.__file__).resolve().parents[1]
    for path in root.rglob("*.py"):
        src = path.read_text(encoding="utf-8")
        if "apply.yaml" in src or "APPLY_CONFIG" in src:
            assert not re.search(r"write_text|safe_dump|yaml\.dump|open\([^)]*['\"][wa]", src), path
        assert not re.search(r"submit_mode\s*\[[^\]]+\]\s*=|\[.submit_mode.\]\s*\[[^\]]+\]\s*=", src), path


# ── 3/4. attempts: modes, evidence, outcomes ─────────────────────────────────

class FakePage:
    def __init__(self, after_submit_url=None, fail_on=None):
        self.url, self.after, self.fail_on, self.filled, self.clicked = "https://form.test/apply/1", after_submit_url, fail_on, {}, False

    def screenshot(self, path, full_page):
        Path(path).write_bytes(b"png")

    def wait_for_timeout(self, ms):
        pass


class FakeBrowser:
    def __init__(self, page):
        self.page, self.read_only_on, self.blocked = page, False, []

    @classmethod
    def factory(cls, **kw):
        made = []

        def make(platform):
            b = cls(FakePage(**kw))
            made.append(b)
            return b
        make.made = made
        return make

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read_only(self):
        self.read_only_on = True
        self.blocked.append("POST https://form.test/analytics")


class FakeSubmit:
    def __init__(self, page):
        self.page = page

    def click(self):
        if self.page.fail_on == "after_click":
            self.page.clicked = True
            raise RuntimeError("navigation timeout")
        self.page.clicked = True
        if self.page.after:
            self.page.url = self.page.after


class FakeApplier(FormApplier):
    def __init__(self, platform="internshala", fields=None):
        self.platform, self.channels = platform, (platform,)
        self._fields = fields or [FormField("Email", "email", True, aa="0"), FormField("Why should we hire you?", "textarea", False, aa="1")]

    def open_form(self, bs, job):
        if bs.page.fail_on == "open":
            raise RuntimeError("form did not load")

    def read_fields(self, bs):
        return list(self._fields)

    def fill(self, bs, field, value):
        bs.page.filled[field.label] = value

    def submit_control(self, bs):
        return FakeSubmit(bs.page)

    def success_assertion(self, bs, before_url):
        return f"URL changed to {bs.page.url}" if bs.page.url.endswith("/success") else None


def answer_all(field, job):
    if field.field_type == "textarea":
        return Answer(park_reason="free text: parks in this phase")
    return Answer("asha@example.edu", "email", "1a", 1.0)


def _run(session, tmp_path, mode="review_only", **page_kw):
    app = _app(session, n=99)
    factory = FakeBrowser.factory(**page_kw)
    cfg = {**CFG, "evidence_dir": str(tmp_path), "submit_mode": {"internshala": mode}}
    res = run_attempt(session, FakeApplier(), app, answer_all, browser_factory=factory, cfg=cfg, now=NOW)
    return app, res.attempt, factory.made[0]


def test_review_only_parks_with_full_evidence_and_never_clicks_submit(session, tmp_path):
    app, a, browser = _run(session, tmp_path)
    assert browser.read_only_on and not browser.page.clicked
    assert (a.mode, a.outcome, a.review_status) == ("review_only", "parked", "pending")
    assert a.park_reason == "review_only: halted at the submit control"
    assert Path(a.screenshot_path).exists() and a.final_url == "https://form.test/apply/1"
    assert a.filled_fields == [{"label": "Email", "canonical_key": "email", "value": "asha@example.edu", "tier": "1a",
                                "confidence": 1.0, "field_type": "email"}]
    assert a.unfilled_fields[0]["reason"] == "free text: parks in this phase"
    assert len(a.form_fingerprint) == 40 and a.blocked_requests
    assert app.status == ApplicationStatus.PARKED and app.last_attempt_at is not None


def test_fingerprint_is_platform_plus_sorted_questions():
    f1 = [FormField("Email", "email"), FormField("Phone", "text")]
    assert harness.fingerprint("internshala", f1) == harness.fingerprint("internshala", list(reversed(f1)))
    assert harness.fingerprint("internshala", f1) != harness.fingerprint("unstop", f1)


def test_live_submitted_only_when_the_assertion_matches(session, tmp_path):
    app, a, browser = _run(session, tmp_path, mode="live", after_submit_url="https://form.test/apply/1/success")
    assert not browser.read_only_on and browser.page.clicked
    assert (a.outcome, a.success_evidence) == ("submitted", "URL changed to https://form.test/apply/1/success")
    assert app.status == ApplicationStatus.SUBMITTED


def test_live_without_matching_assertion_is_uncertain_not_submitted(session, tmp_path):
    app, a, _ = _run(session, tmp_path, mode="live")   # no error, URL unchanged
    assert (a.outcome, a.review_status) == ("uncertain", "pending")
    assert app.status == ApplicationStatus.PARKED


def test_error_after_the_submit_click_is_uncertain(session, tmp_path):
    _, a, _ = _run(session, tmp_path, mode="live", fail_on="after_click")
    assert a.outcome == "uncertain" and "navigation timeout" in a.error


def test_error_before_submitting_fails_and_frees_the_cluster(session, tmp_path):
    app, a, _ = _run(session, tmp_path, fail_on="open")
    assert a.outcome == "failed" and app.status == ApplicationStatus.FAILED
    assert session.query(ApplicationClaim).count() == 0


def test_missing_required_answer_parks_even_in_live_mode(session, tmp_path):
    app = _app(session, n=7)
    applier = FakeApplier(fields=[FormField("Why should we hire you?", "textarea", True, aa="0")])
    factory = FakeBrowser.factory(after_submit_url="https://form.test/apply/1/success")
    a = run_attempt(session, applier, app, answer_all, browser_factory=factory,
                    cfg={**CFG, "evidence_dir": str(tmp_path), "submit_mode": {"internshala": "live"}}, now=NOW).attempt
    assert a.outcome == "parked" and "free text" in a.park_reason and not factory.made[0].page.clicked


def test_resume_is_the_active_vault_resume(session):
    from autoapply.models.vault import VaultIdentity, VaultResume
    ident = VaultIdentity(first_name="A")
    session.add_all([ident, VaultResume(identity=ident, file_path="/r/old.pdf", is_active=False),
                     VaultResume(identity=ident, file_path="/r/cv.pdf", is_active=True)])
    session.commit()
    assert harness.resume_for(session, "ml") == "/r/cv.pdf"


# ── 7. review decisions ──────────────────────────────────────────────────────

def test_review_approve_reject_edit_and_uncertain(session, tmp_path):
    app, a, _ = _run(session, tmp_path)
    review.decide(session, a.id, "edit", {"Why should we hire you?": "Because I build RAG systems."})
    assert a.review_status == "approved" and app.status == ApplicationStatus.QUEUED
    assert {"label": "Why should we hire you?", "canonical_key": None, "value": "Because I build RAG systems.",
            "tier": "review"} in a.filled_fields

    app2 = _app(session, title="ML Intern", company="Other Co", n=50)
    a2 = run_attempt(session, FakeApplier(), app2, answer_all, browser_factory=FakeBrowser.factory(),
                     cfg={**CFG, "daily_cap": 10, "platform_caps": {}, "evidence_dir": str(tmp_path)}, now=NOW).attempt
    review.decide(session, a2.id, "reject")
    assert (a2.review_status, app2.status) == ("rejected", ApplicationStatus.CLOSED)

    app3 = _app(session, title="SDE Intern", company="Third Co", n=60)
    a3 = run_attempt(session, FakeApplier(), app3, answer_all, browser_factory=FakeBrowser.factory(),
                     cfg={**CFG, "daily_cap": 10, "platform_caps": {}, "evidence_dir": str(tmp_path),
                          "submit_mode": {"internshala": "live"}}, now=NOW).attempt
    assert review.items(session, "uncertain")[0]["id"] == a3.id
    review.decide(session, a3.id, "landed")
    assert app3.status == ApplicationStatus.SUBMITTED and a3.success_evidence == "confirmed by the candidate"


# ── 2. apply-link resolver ───────────────────────────────────────────────────

def test_resolver_unstop_is_native_and_cached(session):
    job = Job(title="DS Intern", company="X", source="unstop", is_active=1, location_fit="ok", dedup_hash="u1",
              source_url="https://unstop.com/internships/ds-1")
    session.add(job)
    session.commit()
    res = resolve.run_resolver(session, sources=("unstop",))
    assert res["before"]["unknown"] == 1 and res["after"]["unstop"] == 1 and res["outcomes"] == {"unstop": 1}
    assert job.apply_channel == "unstop" and job.apply_resolved_at is not None
    assert resolve.run_resolver(session, sources=("unstop",))["outcomes"] == {}   # never re-resolved


def test_resolver_himalayas_challenge_is_noted_not_cached(session):
    from autoapply.sources.browser import ChallengeDetected
    job = Job(title="DS Intern", company="X", source="himalayas", is_active=1, location_fit="ok", dedup_hash="h1",
              application_url="https://himalayas.app/companies/x/jobs/ds")
    session.add(job)
    session.commit()

    class Walled:
        page = None
        blocked = []

        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read_only(self): pass
        def goto(self, url): raise ChallengeDetected("himalayas: challenge page: 'Just a moment...'")

    res = resolve.run_resolver(session, sources=("himalayas",), browser_factory=Walled)
    assert res["outcomes"] == {"robots": 1} and job.apply_resolved_at is None
    assert "not fetched" in job.apply_resolve_note


def test_resolver_follows_external_apply_link_and_retags():
    class Page:
        def evaluate(self, js): return [["Apply now", "https://jobs.lever.co/wurl/abc-123"]]

    class BS:
        page = Page()
        def goto(self, url): pass

    robots = resolve.Robots(lambda u: "User-agent: *\nAllow: /\n")
    job = Job(title="DS Intern", company="Wurl", source="himalayas", application_url="https://himalayas.app/companies/wurl/jobs/ds")
    assert resolve.resolve_himalayas(job, BS(), robots, NOW) == "resolved"
    assert (job.apply_channel, job.ats_platform, job.resolved_apply_url) == ("ats_direct", "lever", "https://jobs.lever.co/wurl/abc-123")
    denied = resolve.Robots(lambda u: "User-agent: *\nDisallow: /companies/\n")
    job2 = Job(title="x", company="y", source="himalayas", application_url="https://himalayas.app/companies/a/jobs/b")
    assert resolve.resolve_himalayas(job2, BS(), denied, NOW) == "robots" and job2.apply_resolved_at is None


def test_robots_rules_match_crawler_semantics():
    from autoapply.appliers.resolve import RobotsRules
    text = ("User-Agent: *\n\nDisallow: /*?*\nDisallow: /student\nDisallow: /application/*\n\nAllow: /favicon.ico?*\n\n"
            "User-agent: ClaudeBot\nDisallow: /\n")
    r = RobotsRules(text)          # the blank line after User-Agent does not end the group
    assert r.allowed("https://x.com/internship/detail/a-1")
    assert not r.allowed("https://x.com/internship/detail/a-1?utm_source=x")
    assert not r.allowed("https://x.com/student/interstitial/application/a")
    assert not r.allowed("https://x.com/application/form/a")
    assert r.allowed("https://x.com/favicon.ico?v=2")                     # longer Allow beats Disallow
    assert not r.allowed("https://x.com/internships/", "ClaudeBot")


def test_resume_field_prefers_the_resume_over_a_photo_or_autofill_box():
    pick = FakeApplier().resume_field
    assert pick([FormField("Upload profile image", "file"), FormField("Resume", "file")]).label == "Resume"
    assert pick([FormField("Application", "file"), FormField("Cover Letter", "file"), FormField("CV", "file")]).label == "CV"
    assert pick([FormField("Attach", "file"), FormField("Attach", "file")]) is not None
    assert pick([FormField("Email", "email")]) is None
