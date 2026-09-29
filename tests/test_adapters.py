"""Source adapters against recorded HTTP fixtures (tests/fixtures/<source>/). Never live.

Every adapter gets an HttpClient whose transport replays fixtures; a request the test didn't
expect fails the test, so a network call can't slip through.
"""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables)
from autoapply.config import (
    AdzunaSearch, HimalayasSearch, InstahyreSearch, InternshalaSearch, UnstopSearch, load_search_config,
)
from autoapply.models.base import Base
from autoapply.models.company import Company
from autoapply.models.job import Job
from autoapply.services.job_service import ingest_job
from autoapply.sources import adzuna, unstop
from autoapply.sources.adzuna import AdzunaSource
from autoapply.sources.filter import evaluate_job
from autoapply.sources.himalayas import HimalayasSource
from autoapply.sources.http_client import HttpClient
from autoapply.sources.instahyre import InstahyreSource
from autoapply.sources.internshala import InternshalaSource, parse_ago, parse_apply_by
from autoapply.sources.unstop import UnstopSource

FIXTURES = Path(__file__).parent / "fixtures"


def load(path: str) -> str:
    return (FIXTURES / path).read_text(encoding="utf-8")


def replay(route):
    """HttpClient whose responses come from route(request) -> (status, body) ."""
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        status, body = route(request)
        return httpx.Response(status, text=body)

    client = HttpClient(requests_per_second=1000, max_retries=0, transport=httpx.MockTransport(handler))
    return client, requests


def unexpected(request):
    pytest.fail(f"unexpected request: {request.url}")


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def ingest_all(session, results):
    cfg = load_search_config()
    for r in results:
        data = r.to_dict()
        data.update(evaluate_job(data, cfg))
        ingest_job(session, data)


# ── Unstop ───────────────────────────────────────────────────────────────────

def _unstop_route(request):
    if request.url.host != "unstop.com" or request.url.path != "/api/public/opportunity/search-result":
        unexpected(request)
    assert request.url.params["oppstatus"] == "open"
    return 200, load(f"unstop/page{request.url.params['page']}.json")


def test_unstop_paginates_and_maps_fields():
    client, requests = replay(_unstop_route)
    results = list(UnstopSource(UnstopSearch(max_pages=5), http=client).discover())

    assert [r.url.params["page"] for r in requests] == ["1", "2"]  # stops when next_page_url is null
    by_title = {r.title: r for r in results}
    assert len(results) == 3

    wfh = by_title["Social Media Marketing Internship"]
    assert (wfh.work_mode, wfh.location, wfh.compensation_text) == ("remote", "Remote", "Unpaid")
    assert wfh.source_url.startswith("https://unstop.com/internships/")

    # region=online, but jobDetail.type=hybrid is the real work mode.
    hybrid = by_title["Artificial Intelligence Internship"]
    assert hybrid.raw_data["region"] == "online"
    assert (hybrid.work_mode, hybrid.location) == ("hybrid", "Hyderabad")
    assert hybrid.company_meta == {"is_india": True, "india_cities": ["Hyderabad"]}

    closed = by_title["Data Science Intern"]  # recorded with regn_open=0
    assert closed.company == "Microsoft" and closed.expired is True
    assert all(not r.expired for r in results if r is not closed)
    assert all(r.employment_type == "Internship" and r.description_text for r in results)


def test_unstop_stipend_and_deadline_quirks():
    item = copy.deepcopy(json.loads(load("unstop/page1.json"))["data"]["data"][0])
    item["jobDetail"].update(paid_unpaid="paid", pay_in="annually", min_salary=8000, max_salary=10000,
                             not_disclosed=False)
    # pay_in defaults to "annually" on Unstop; ₹8,000-10,000 for an internship is monthly.
    assert unstop.parse_item(item).compensation_text == "INR 8,000 - 10,000 /month"
    item["jobDetail"].update(min_salary=300000, max_salary=500000)
    assert unstop.parse_item(item).compensation_text == "INR 300,000 - 500,000 /year"

    # regn_open stays 1 on expired listings; the deadline decides.
    later = datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert item["regn_open"] == 1 and unstop.parse_item(item, now=later).expired is True


def test_unstop_ingest_feeds_companies_and_closes_expired(session):
    client, _ = replay(_unstop_route)
    ingest_all(session, UnstopSource(UnstopSearch(), http=client).discover())

    assert session.query(Job).count() == 3
    closed = session.query(Job).filter_by(title="Data Science Intern").one()
    assert closed.closed_at is not None and closed.is_active == 0 and closed.applications == []
    xtragrad = session.query(Company).filter(Company.name.like("Xtragrad%")).one()
    assert xtragrad.is_india and xtragrad.india_cities == ["Hyderabad"] and xtragrad.discovered_via == "unstop"
    assert all(j.company_id for j in session.query(Job))


# ── Internshala ──────────────────────────────────────────────────────────────

def _internshala_route(request):
    path = request.url.path
    if path == "/internships/computer-science-internship/":
        return 200, load("internshala/listing_page1.html")
    if path == "/internships/computer-science-internship/page-2/":
        return 200, load("internshala/listing_page1.html")  # past the end the site repeats cards
    # Detail URLs end in a posting number, not the internshipid.
    for slug, iid in (("at-humanity-founders", "3305513"), ("at-apni-pathshala", "3279403")):
        if path.startswith("/internship/detail/") and slug in path:
            return 200, load(f"internshala/detail_{iid}.html")
    unexpected(request)


def test_internshala_cards_details_and_cache(tmp_path):
    cfg = InternshalaSearch(categories=["computer-science-internship"], max_pages=5)
    client, requests = replay(_internshala_route)
    source = InternshalaSource(cfg, search_cfg=load_search_config(), http=client, cache_dir=tmp_path)
    results = {r.source_id: r for r in source.discover()}

    assert set(results) == {"3305513", "3279403", "3273266"}
    listing_paths = [r.url.path for r in requests if r.url.path.startswith("/internships/")]
    assert listing_paths == ["/internships/computer-science-internship/",
                             "/internships/computer-science-internship/page-2/"]  # page 2 added nothing new

    remote = results["3305513"]
    assert (remote.title, remote.company, remote.work_mode, remote.location) == (
        "React Native Development", "Humanity Founders", "remote", "Remote")
    assert remote.compensation_text == "₹ 4,000 - 8,000 /month"
    assert remote.description_text.startswith("About the work from home job/internship")
    assert remote.deadline == datetime(2026, 10, 28, tzinfo=timezone.utc)

    mumbai = results["3279403"]
    assert mumbai.work_mode == "onsite" and mumbai.company_meta["india_cities"] == ["Mumbai"]
    assert mumbai.company_meta["domain"] == "apnipathshala.org"
    assert "Duration: 6 Months" in mumbai.description_text

    # Digital Marketing fails the filter: no detail fetch, card data only.
    marketing = results["3273266"]
    assert marketing.deadline is None and marketing.description_raw is None
    detail_paths = [r.url.path for r in requests if r.url.path.startswith("/internship/detail/")]
    assert len(detail_paths) == 2 and not any("smart-suburbs" in p for p in detail_paths)
    assert source.stats == {"cards": 3, "details_fetched": 2, "details_cached": 0, "details_skipped": 1}

    # Second run: details come from the on-disk cache.
    client2, requests2 = replay(_internshala_route)
    source2 = InternshalaSource(cfg, search_cfg=load_search_config(), http=client2, cache_dir=tmp_path)
    again = {r.source_id: r for r in source2.discover()}
    assert not any(r.url.path.startswith("/internship/detail/") for r in requests2)
    assert again["3279403"].description_text == mumbai.description_text
    assert source2.stats["details_cached"] == 2


def test_internshala_date_helpers():
    now = datetime(2026, 9, 30, tzinfo=timezone.utc)
    assert parse_ago("2 weeks ago", now) == datetime(2026, 9, 16, tzinfo=timezone.utc)
    assert parse_ago("Just now", now) == now and parse_ago("Few hours ago", now) == now
    assert parse_ago("Actively hiring", now) is None
    assert parse_apply_by("APPLY BY 12 Oct' 26") == datetime(2026, 10, 12, tzinfo=timezone.utc)


# ── Instahyre ────────────────────────────────────────────────────────────────

def _instahyre_route(request):
    if request.url.path != "/api/v1/job_search":
        unexpected(request)
    assert request.url.params["experience_level"] == "internship"
    offset = int(request.url.params["offset"])
    return 200, load("instahyre/page1.json" if offset == 0 else "instahyre/page2.json")


def test_instahyre_pages_by_offset_and_seeds_companies(session):
    client, requests = replay(_instahyre_route)
    results = list(InstahyreSource(InstahyreSearch(), http=client).discover())

    assert [r.url.params["offset"] for r in requests] == ["0", "35"]  # meta.next is null on page 2
    assert [r.title for r in results] == ["AI Business & Operations Analyst", "Angular Developer",
                                          "Technical Sales Intern"]
    assert all(r.employment_type == "Internship" for r in results)
    assert results[2].work_mode == "remote"
    assert results[1].company_meta["india_cities"] == ["Bengaluru"]

    ingest_all(session, results)
    baygrape = session.query(Company).filter_by(name="Baygrape Technology Solutions").one()
    assert baygrape.about_text.startswith("Transformational Technologies")
    assert baygrape.india_cities == ["Bengaluru"] and baygrape.discovered_via == "instahyre"


# ── Himalayas ────────────────────────────────────────────────────────────────

def _himalayas_route(request):
    if request.url.path != "/jobs/api/search":
        unexpected(request)
    assert request.url.params["employment_type"] == "Intern"
    return 200, load("himalayas/search_page1.json" if request.url.params["page"] == "1"
                     else "himalayas/search_empty.json")


def test_himalayas_search_until_empty_page():
    client, requests = replay(_himalayas_route)
    results = list(HimalayasSource(HimalayasSearch(), http=client).discover())

    assert [r.url.params["page"] for r in requests] == ["1", "2"]
    india, us = results
    assert (india.location, india.work_mode, india.compensation_text) == ("Remote - India", "remote", "INR 32,000 /month")
    assert india.company_meta == {"is_india": True}
    assert (us.location, us.compensation_text, us.company_meta) == ("Remote - United States", "USD 23 /hour", None)
    assert all(r.employment_type == "Intern" and r.description_text and not r.expired for r in results)


def test_himalayas_cursor_feed_is_opt_in():
    feed = {"jobs": json.loads(load("himalayas/search_page1.json"))["jobs"][:1], "nextCursor": None}

    def route(request):
        if request.url.path == "/jobs/api/search":
            return 200, load("himalayas/search_empty.json")
        if request.url.path == "/jobs/api":
            assert "cursor" not in request.url.params  # first feed page
            return 200, json.dumps(feed)
        unexpected(request)

    client, requests = replay(route)
    assert list(HimalayasSource(HimalayasSearch(), http=client).discover()) == []
    assert not any(r.url.path == "/jobs/api" for r in requests)
    client, _ = replay(route)
    assert len(list(HimalayasSource(HimalayasSearch(feed_pages=3), http=client).discover())) == 1


# ── Adzuna ───────────────────────────────────────────────────────────────────

def test_adzuna_skips_without_credentials(monkeypatch):
    monkeypatch.delenv("ADZUNA_APP_ID", raising=False)
    monkeypatch.delenv("ADZUNA_APP_KEY", raising=False)
    client, requests = replay(unexpected)
    assert list(AdzunaSource(AdzunaSearch(), http=client).discover()) == []
    assert requests == []


def test_adzuna_parses_documented_shape(monkeypatch):
    monkeypatch.setenv("ADZUNA_APP_ID", "fixture-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "fixture-key")

    def route(request):
        if request.url.host != "api.adzuna.com" or request.url.path != "/v1/api/jobs/in/search/1":
            unexpected(request)
        assert request.url.params["app_id"] == "fixture-id" and request.url.params["what"] == "intern"
        return 200, load("adzuna/page1.json")

    client, requests = replay(route)
    results = list(AdzunaSource(AdzunaSearch(queries=["intern"]), http=client).discover())

    assert len(requests) == 1  # 2 results < results_per_page: last page
    ml, swe = results
    assert (ml.company, ml.location, ml.compensation_text) == (
        "Fixture Analytics Pvt Ltd", "Mumbai, Maharashtra", "INR 180,000 - 240,000 /year")
    assert ml.company_meta == {"india_cities": ["Mumbai"]}
    assert swe.compensation_text is None and swe.salary_min is None  # predicted salary dropped
    assert adzuna.credentials() == ("fixture-id", "fixture-key")
