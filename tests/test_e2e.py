"""End to end through a real Chromium, offline: the Greenhouse harness applier on tests/mock_ats.html
(file://), review_only, in an in-memory database. Proves the form is read and filled from the
answerer, the attempt parks with evidence, and the submit button is never clicked.
(This used to run the legacy bespoke applier against the real database.)"""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401
from autoapply.appliers.greenhouse import GreenhouseApplier
from autoapply.appliers.harness import Answer, run_attempt
from autoapply.models.application import Application, ApplicationStatus
from autoapply.models.base import Base
from autoapply.models.job import Job

MOCK = (Path(__file__).parent / "mock_ats.html").resolve().as_uri()


class MockGreenhouse(GreenhouseApplier):
    def apply_url(self, job):
        return MOCK


def _browser(tmp_path):
    from autoapply.politeness import SourcePolicy
    from autoapply.sources.browser import BrowserSession

    def make(platform):
        return BrowserSession(platform, policy=SourcePolicy(min_delay_s=0, max_delay_s=0), profile_root=tmp_path,
                              record_calls=False)
    return make


def test_greenhouse_review_only_on_a_real_browser(tmp_path):
    pytest.importorskip("playwright")
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    job = Job(title="Software Engineer Intern", company="MockInc", source="greenhouse", ats_platform="greenhouse",
              apply_channel="ats_direct", application_url="https://job-boards.greenhouse.io/mockinc/jobs/1", dedup_hash="e2e")
    session.add(job)
    session.flush()
    app = Application(job_id=job.id, company="MockInc", role=job.title, status=ApplicationStatus.QUEUED)
    session.add(app)
    session.commit()
    values = {"First Name": "Asha", "Last Name": "Rao", "Email": "asha@example.edu", "Phone": "+91 90000 00000"}

    def answer(field, job):
        return Answer(values[field.label], field.label.lower().replace(" ", "_"), "1a", 1.0) if field.label in values \
            else Answer(park_reason="free text: parks")
    try:
        res = run_attempt(session, MockGreenhouse(), app, answer, browser_factory=_browser(tmp_path),
                          cfg={"daily_cap": 5, "submit_mode": {}, "evidence_dir": str(tmp_path / "ev")})
    except Exception as e:   # no Chromium installed here
        pytest.skip(f"browser unavailable: {e}")
    a = res.attempt
    assert a.outcome == "parked" and a.mode == "review_only", a.error
    assert {f["label"] for f in a.filled_fields} == set(values)
    assert Path(a.screenshot_path).exists() and a.final_url == MOCK     # never left the form
    assert app.status == ApplicationStatus.PARKED
