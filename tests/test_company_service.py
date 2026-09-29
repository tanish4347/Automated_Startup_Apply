import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables)
from autoapply.config import PROJECT_ROOT
from autoapply.models.base import Base
from autoapply.models.company import PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_NORMAL, Company
from autoapply.services.company_service import get_or_create_company


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _by_name(session, name):
    return session.query(Company).filter_by(name=name).one()


def test_career_pages_migrated_into_companies_yaml(session):
    """career_pages.json is gone; its 276 entries (2 duplicates) live on in config/companies.yaml."""
    from autoapply.discovery.universe import ManualSeeder, run_seeder
    stats = run_seeder(session, ManualSeeder(PROJECT_ROOT / "config" / "companies.yaml"))
    assert stats.created >= 274
    razorpay = _by_name(session, "Razorpay")
    assert (razorpay.is_india, razorpay.priority, razorpay.ats_token) == (True, PRIORITY_HIGH, "razorpay")
    for off_target in ("Abbvie", "Perryhomes", "Wabashvalleypoweralliance"):
        c = _by_name(session, off_target)
        assert (bool(c.is_india), c.priority) == (False, PRIORITY_LOW)
    openai = _by_name(session, "OpenAI")
    assert (bool(openai.is_india), openai.priority, openai.ats_token) == (False, PRIORITY_NORMAL, "openai")
    assert _by_name(session, "Semgrep").priority == PRIORITY_NORMAL  # software startup from the US tail
    assert (_by_name(session, "Wabtec").ats_type, _by_name(session, "Wabtec").ats_token) == ("smartrecruiters", "Wabtec")


def test_get_or_create_matches_legal_names_and_keeps_known_ats(session):
    a = get_or_create_company(session, "Groww", discovered_via="linkedin", ats_type="greenhouse")
    b = get_or_create_company(session, "Groww Pvt Ltd", ats_type="lever")
    assert a.id == b.id
    assert a.ats_type == "greenhouse"
    assert get_or_create_company(session, "  ") is None


def test_company_meta_fills_gaps_only(session):
    a = get_or_create_company(session, "Acme Labs", meta={
        "domain": "www.Acme.io", "about": "first", "is_india": True, "india_cities": ["Mumbai"]})
    assert (a.domain, a.about_text, a.is_india, a.india_cities) == ("acme.io", "first", True, ["Mumbai"])

    again = get_or_create_company(session, "ACME LABS", meta={
        "domain": "other.io", "about": "second", "is_india": False, "india_cities": ["Pune", "Mumbai"]})
    assert again is a
    assert (a.domain, a.about_text, a.is_india) == ("acme.io", "first", True)  # never overwritten
    assert a.india_cities == ["Mumbai", "Pune"]  # accumulates

    # domain is unique: a differently named company can't claim it.
    b = get_or_create_company(session, "Beta Corp", meta={"domain": "acme.io"})
    assert b.domain is None
