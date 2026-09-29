import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables)
from autoapply.config import PROJECT_ROOT
from autoapply.models.base import Base
from autoapply.models.company import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_NORMAL, Company
from autoapply.services.company_service import get_or_create_company, import_career_pages


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _by_name(session, name):
    return session.query(Company).filter_by(name=name).one()


def test_real_career_pages_import(session):
    stats = import_career_pages(session, PROJECT_ROOT / "career_pages.json")
    # 276 entries, Plaid and GitLab listed twice.
    assert stats["created"] == 274 and stats["skipped_duplicates"] == 2
    razorpay = _by_name(session, "Razorpay")
    assert (razorpay.is_india, razorpay.priority, razorpay.ats_token) == (True, PRIORITY_HIGH, "razorpay")
    for off_target in ("Abbvie", "Perryhomes", "Wabashvalleypoweralliance"):
        c = _by_name(session, off_target)
        assert (c.is_india, c.priority) == (False, PRIORITY_LOW)
    openai = _by_name(session, "OpenAI")
    assert (openai.is_india, openai.priority) == (False, PRIORITY_NORMAL)
    assert _by_name(session, "Semgrep").priority == PRIORITY_NORMAL  # software startup from the US tail


def test_import_is_idempotent(session, tmp_path):
    f = tmp_path / "c.json"
    f.write_text(json.dumps([{"company": "CRED", "board_token": "cred"}]))
    import_career_pages(session, f)
    stats = import_career_pages(session, f)
    assert (stats["created"], stats["updated"]) == (0, 1)
    assert session.query(Company).count() == 1


def test_get_or_create_matches_legal_names_and_keeps_known_ats(session):
    a = get_or_create_company(session, "Groww", discovered_via="linkedin", ats_type="greenhouse")
    b = get_or_create_company(session, "Groww Pvt Ltd", ats_type="lever")
    assert a.id == b.id
    assert a.ats_type == "greenhouse"
    assert get_or_create_company(session, "  ") is None
