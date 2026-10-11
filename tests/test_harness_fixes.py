import pytest
from unittest.mock import MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from autoapply.models.base import Base

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()
from autoapply.appliers.harness import run_attempt, FormApplier, FormField, AttemptResult, Answer
from autoapply.models.application import Application, ApplicationStatus
from autoapply.models.job import Job
from autoapply.models.attempt import FAILED, REVIEW_ONLY, SUBMITTED
from autoapply.sources.browser import ChallengeDetected
from autoapply.appliers.smartrecruiters import SmartRecruitersApplier

class MockPage:
    def __init__(self):
        self.url = "http://example.com/apply"
        self.locators = {}
        self.timeouts = 0
    def locator(self, sel):
        m = MagicMock()
        m.count.return_value = 1
        return m
    def wait_for_timeout(self, ms):
        self.timeouts += ms
    def evaluate(self, js):
        return {"fields": [{"label": "Graduation Year", "field_type": "number", "aa": "grad-year", "control": "number"}]}
    def get_by_role(self, role, name=None, exact=False):
        m = MagicMock()
        m.count.return_value = 0
        return m
    def screenshot(self, path, full_page):
        pass

class MockBrowserSession:
    def __init__(self, platform):
        self.platform = platform
        self.page = MockPage()
        self.blocked = []
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass
    def read_only(self, allow=None):
        pass
    def goto(self, url):
        pass

def test_challenge_detected_caught(session):
    job = Job(id=1, apply_channel="smartrecruiters", source="test", title="T", company="C")
    app = Application(id=1, job=job, status=ApplicationStatus.QUEUED)
    session.add(job)
    session.add(app)
    session.commit()

    class ThrowingApplier(SmartRecruitersApplier):
        def open_form(self, bs, job):
            raise ChallengeDetected("mock challenge")

    def mock_answerer(fld, job): return Answer(None)
    
    cfg = {"daily_cap": 100, "evidence_dir": "data/evidence"}
    res = run_attempt(session, ThrowingApplier(), app, mock_answerer, browser_factory=MockBrowserSession, cfg=cfg)
    
    assert res.attempt.outcome == FAILED
    assert "CHALLENGE_DETECTED: mock challenge" in res.attempt.error
    assert app.status == ApplicationStatus.FAILED

def test_numeric_field_type_safety(session):
    job = Job(id=2, apply_channel="test", source="test", title="T", company="C")
    app = Application(id=2, job=job, status=ApplicationStatus.QUEUED)
    session.add(job)
    session.add(app)
    session.commit()

    class TestApplier(FormApplier):
        platform = "test"
        max_steps = 1
        def open_form(self, bs, job): pass
        def submit_control(self, bs): return bs.page.locator("button")
        def success_assertion(self, bs, before_url): return "success"
    
    applier = TestApplier()
    fld = FormField(label="Graduation Year", field_type="number", control="number", aa="123")
    
    bs = MockBrowserSession("test")
    
    # Send a dictionary (e.g. date range) to a numeric field
    val = {"start": "2020-01-01", "end": "2024-05-01"}
    
    bs.page.locator = MagicMock()
    m_loc = MagicMock()
    m_loc.evaluate.return_value = {"value": "2024"}
    bs.page.locator.return_value = m_loc
    
    applier.fill(bs, fld, val)
    m_loc.fill.assert_called_with("2024")
