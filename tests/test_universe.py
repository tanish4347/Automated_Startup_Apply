"""Company universe: dedup/merge audit, portfolio extraction strategies, directory seeders.
Fixtures in tests/fixtures/universe/ are trimmed real pages (2026-09-30); no network."""

import json
from pathlib import Path

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables)
from autoapply.discovery.directories import Inc42Seeder, StartupIndiaSeeder, YCSeeder, parse_inc42_company
from autoapply.discovery.github import GitHubOrgSeeder
from autoapply.discovery.portfolios import PortfolioSeeder, PortfolioSpec, extract, website_from_detail
from autoapply.discovery.universe import (
    ManualSeeder, OwnDataSeeder, Seed, SeedContext, domain_of, run_seeder, universe_stats, write_seed,
)
from autoapply.models.base import Base
from autoapply.models.company import PRIORITY_HIGH, Company, CompanyAlias
from autoapply.services.job_service import ingest_job
from autoapply.sources.http_client import HttpClient

FIXTURES = Path(__file__).parent / "fixtures" / "universe"


def load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def replay(route):
    seen = []

    def handler(request):
        seen.append(request)
        status, body = route(request)
        return httpx.Response(status, text=body)

    return HttpClient(requests_per_second=1000, max_retries=0, transport=httpx.MockTransport(handler)), seen


# ── dedup and the merge audit ────────────────────────────────────────────────

def test_domain_of():
    assert domain_of("https://www.Razorpay.com/careers?x=1") == "razorpay.com"
    assert domain_of("razorpay.com") == "razorpay.com"
    assert domain_of("https://in.linkedin.com/company/razorpay") is None
    assert domain_of("https://play.google.com/store/apps/details?id=x") is None
    assert domain_of("") is None and domain_of("not a url") is None


def test_write_seed_dedups_by_domain_then_name_and_logs_merges(session):
    assert write_seed(session, Seed("Razorpay", website="https://razorpay.com", is_india=True), "yc") == "created"
    # Same company, different spelling, same domain: merged on domain.
    assert write_seed(session, Seed("Razorpay Software Pvt. Ltd.", website="https://www.razorpay.com/"), "inc42") == "merged"
    # Same normalized name, no website: merged on name.
    assert write_seed(session, Seed("Razorpay Pvt Ltd"), "own_data") == "merged"
    # A case-only difference is the same spelling: not an alias worth auditing.
    assert write_seed(session, Seed("RAZORPAY"), "portfolios") == "existing"
    assert write_seed(session, Seed("   "), "manual") == "skipped"

    company = session.query(Company).one()
    assert company.seed_sources == ["yc", "inc42", "own_data", "portfolios"]
    assert (company.domain, company.website, company.is_india) == ("razorpay.com", "https://razorpay.com", True)
    aliases = {(a.alias, a.match_type, a.evidence) for a in session.query(CompanyAlias)}
    assert aliases == {("Razorpay Software Pvt. Ltd.", "domain", "razorpay.com"), ("Razorpay Pvt Ltd", "name", "razorpay")}


def test_high_priority_seeds_and_meta(session):
    write_seed(session, Seed("Sarvam AI", website="sarvam.ai", india_cities=["Bengaluru"], high_priority=True), "manual")
    c = session.query(Company).one()
    assert c.priority == PRIORITY_HIGH and c.domain == "sarvam.ai" and c.website == "https://sarvam.ai"
    assert c.india_cities == ["Bengaluru"]


def test_own_data_seeder_marks_india_only_when_all_jobs_are_in_india(session):
    ingest_job(session, {"title": "ML Intern", "company": "Desi Labs", "country": "India", "city": "Mumbai"})
    ingest_job(session, {"title": "SDE Intern", "company": "Desi Labs", "country": "India", "city": "Pune"})
    ingest_job(session, {"title": "ML Intern", "company": "Global Corp", "country": "India", "city": "Pune"})
    ingest_job(session, {"title": "SWE Intern", "company": "Global Corp", "country": "United States"})
    stats = run_seeder(session, OwnDataSeeder())
    assert (stats.seen, stats.existing) == (2, 2)
    desi = session.query(Company).filter_by(name="Desi Labs").one()
    glob = session.query(Company).filter_by(name="Global Corp").one()
    assert desi.is_india and desi.india_cities == ["Mumbai", "Pune"] and desi.seed_sources == ["own_data"]
    assert not glob.is_india


def test_manual_seeder(tmp_path, session):
    path = tmp_path / "companies.yaml"
    path.write_text("- Plain Name Co\n- {name: Sarvam AI, website: https://www.sarvam.ai, india: true, priority: true}\n")
    stats = run_seeder(session, ManualSeeder(path))
    assert stats.created == 2
    assert session.query(Company).filter_by(name="Sarvam AI").one().priority == PRIORITY_HIGH
    assert ManualSeeder(tmp_path / "missing.yaml").available()


# ── portfolio strategies (real page snippets) ────────────────────────────────

def test_internal_strategy_names_from_logo_alt():
    spec = PortfolioSpec(name="Peak XV", url="https://www.peakxv.com/our-companies/", strategy="internal",
                         name_selector="img@alt")
    entries = extract(load("peakxv_listing.html"), spec.url, spec)
    # Each card has two links to the same page (logo and READ): one entry per company.
    assert [e["name"] for e in entries] == ["Supabase", "Twin Health"]
    assert entries[0]["detail"] == "https://www.peakxv.com/companies/supabase"  # nav link ignored


def test_website_from_detail_skips_social_and_chrome():
    spec = PortfolioSpec(name="Peak XV", url="https://www.peakxv.com/our-companies/")
    assert website_from_detail(load("peakxv_detail.html"), "https://www.peakxv.com/companies/supabase",
                               spec) == "https://www.supabase.com"


def test_links_strategy():
    spec = PortfolioSpec(name="Better Capital", url="https://bettercapital.vc/portfolio", strategy="links")
    entries = extract(load("bettercapital_listing.html"), spec.url, spec)
    # The image-only link and the text link to 1cell.ai collapse into one entry, named by the text.
    assert [e["name"] for e in entries] == ["1Cell", "Accounti", "Aina"]
    assert all(e["url"].startswith("http") and "linkedin" not in e["url"] for e in entries)


def test_jsonld_strategy_turns_own_site_urls_into_detail_pages():
    spec = PortfolioSpec(name="Stellaris", url="https://www.stellarisvp.com/portfolio", strategy="jsonld")
    entries = extract(load("stellaris_listing.html"), spec.url, spec)
    assert len(entries) == 3 and all(e["detail"].startswith("https://www.stellarisvp.com/portfolio/") for e in entries)
    assert "Stellaris" not in {e["name"] for e in entries}


def test_regex_strategy_reads_embedded_cms_objects():
    import yaml
    specs = {s["name"]: s for s in yaml.safe_load(Path("config/portfolios.yaml").read_text())}
    spec = PortfolioSpec.model_validate(specs["Accel"])
    entries = extract(load("accel_listing.html"), spec.url, spec)
    assert [e["name"] for e in entries] == ["Anthropic", "Celonis"]
    assert entries[0]["url"].startswith("http") and "anthropic" in entries[0]["url"]


def test_portfolio_seeder_follows_details_for_websites(session):
    def route(r):
        if r.url.path == "/our-companies/":
            return 200, load("peakxv_listing.html")
        if r.url.path == "/companies/supabase":
            return 200, load("peakxv_detail.html")
        return 404, ""

    client, seen = replay(route)
    spec = PortfolioSpec(name="Peak XV", url="https://www.peakxv.com/our-companies/", strategy="internal",
                         name_selector="img@alt", india=False, max_details=1)
    stats = run_seeder(session, PortfolioSeeder([spec], http=client))
    assert stats.created == 2 and len(seen) == 2  # listing + 1 detail page (max_details cap)
    assert session.query(Company).filter_by(name="Twin Health").one().domain is None
    supabase = session.query(Company).filter_by(name="Supabase").one()
    assert supabase.domain == "supabase.com" and supabase.priority == PRIORITY_HIGH and not supabase.is_india


# ── directories ──────────────────────────────────────────────────────────────

def test_yc_seeder_keeps_active_india_companies(session):
    client, _ = replay(lambda r: (200, load("yc_all.json")))
    stats = run_seeder(session, YCSeeder(http=client), {"statuses": ["Active", "Public"]})
    # Razorpay: India, Active. Markupwand: India but Inactive. Aptible: "Indianapolis" is not India.
    assert stats.created == 1
    rzp = session.query(Company).one()
    assert (rzp.name, rzp.domain, rzp.is_india, rzp.india_cities) == ("Razorpay", "razorpay.com", True, ["Bengaluru"])


def test_inc42_company_page():
    info = parse_inc42_company(load("inc42_company_me.html"))
    assert info["name"] == "&ME" and info["website"] == "https://andme.in"
    assert info["industry"] == "Ecommerce" and info["legal_name"].startswith("Merhaki")


def test_inc42_seeder_is_resumable(tmp_path, session):
    index = '<sitemapindex><sitemap><loc>https://inc42.com/datalabs-company-1.xml</loc></sitemap></sitemapindex>'
    urlset = "<urlset>" + "".join(f"<url><loc>https://inc42.com/company/c{i}/</loc></url>" for i in range(3)) + "</urlset>"

    def route(r):
        if r.url.path == "/inc42_datalabs.xml":
            return 200, index
        if r.url.path == "/datalabs-company-1.xml":
            return 200, urlset
        return 200, load("inc42_company_me.html")

    client, seen = replay(route)
    seeder = Inc42Seeder(http=client)
    from autoapply.discovery import universe
    ctx = SeedContext(session=session, cache_dir=tmp_path, options={"max_pages": 2})
    assert len(list(seeder.seeds(ctx))) == 2
    assert len(list(seeder.seeds(ctx))) == 1   # picks up where the last run stopped
    assert list(seeder.seeds(ctx)) == []
    assert (tmp_path / "inc42_done.txt").read_text().count("\n") == 3


def test_startup_india_unreachable_is_skipped_quietly(session):
    client, _ = replay(lambda r: (503, "<h1>Oops! Something went wrong...</h1>"))
    stats = run_seeder(session, StartupIndiaSeeder(http=client))
    assert stats.seen == 0


def test_github_seeder_needs_a_token(monkeypatch):
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.setattr(GitHubOrgSeeder, "_token", staticmethod(lambda: None))
    assert GitHubOrgSeeder().available() == "GITHUB_TOKEN not set in .env"


def test_github_seeder_keeps_orgs_with_websites(tmp_path, session):
    def route(r):
        if r.url.path == "/search/users":
            return 200, json.dumps({"items": [{"login": "acme-in", "type": "Organization"},
                                              {"login": "college-club", "type": "Organization"}]})
        if r.url.path == "/orgs/acme-in":
            return 200, json.dumps({"login": "acme-in", "name": "Acme India", "blog": "https://acme.in",
                                    "location": "Pune, India", "public_repos": 12})
        return 200, json.dumps({"login": "college-club", "name": "Club", "blog": "", "location": "India"})

    client, seen = replay(route)
    ctx = SeedContext(session=session, cache_dir=tmp_path, options={"cities": ["Pune"]})
    seeds = list(GitHubOrgSeeder(http=client, sleep=lambda s: None).seeds(ctx))
    assert [(s.name, s.website, s.india_cities) for s in seeds] == [("Acme India", "https://acme.in", ["Pune"])]
    assert 'location:"Pune"' in seen[0].url.params["q"]
    assert json.loads((tmp_path / "github_orgs.json").read_text())["acme-in"]["name"] == "Acme India"


def test_universe_stats(session):
    write_seed(session, Seed("Razorpay", website="razorpay.com", is_india=True), "yc")
    write_seed(session, Seed("Stripe", website="stripe.com"), "portfolios")
    write_seed(session, Seed("Razorpay Software", website="razorpay.com"), "inc42")
    session.commit()
    st = universe_stats(session)
    assert (st["total"], st["india"], st["with_website"], st["aliases"]) == (2, 1, 2, 1)
    assert st["by_seed_source"] == {"yc": 1, "portfolios": 1, "inc42": 1}
