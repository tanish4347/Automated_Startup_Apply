"""The company universe: every company worth watching for internships, not just the ones that
happened to post on a job board this week.

Seeders produce Seeds (name + website + what the seeder knows). Every seed is written through
write_seed(), which dedups hard:
  1. by website domain (the strongest key: five spellings of one company share one domain),
  2. then by normalized_name (services.dedup.normalize_company),
and records each merge of a differently spelled name as a CompanyAlias row, so false merges can
be audited (`autoapply universe merges`). Every seeder that produced a company is appended to
companies.seed_sources.

Seeders, in run order (own_data first: it is free):
  own_data    company names from ingested jobs
  manual      config/companies.yaml, edited by hand
  portfolios  VC portfolio pages, config/portfolios.yaml (discovery/portfolios.py)
  yc          YC company directory, India only (discovery/directories.py)
  inc42       Inc42 Datalabs company pages (discovery/directories.py)
  startup_india  Startup India directory, when reachable (discovery/directories.py)
  github      GitHub orgs located in India, needs GITHUB_TOKEN (discovery/github.py)
"""

from __future__ import annotations

import abc
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

import yaml
from sqlalchemy.orm import Session

from autoapply.config import DATA_DIR, PROJECT_ROOT
from autoapply.logging import get_logger
from autoapply.models.company import ATS_TYPES, PRIORITY_HIGH, PRIORITY_LOW, Company, CompanyAlias
from autoapply.services.company_service import _apply_meta, get_company, get_or_create_company
from autoapply.services.dedup import normalize_company

log = get_logger(__name__)

CONFIG_DIR = PROJECT_ROOT / "config"

# Hosts that are never a company's own website: social networks, aggregators, app stores,
# link shorteners, site builders' default domains.
NOT_COMPANY_DOMAINS = (
    "linkedin.com", "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com", "youtu.be",
    "github.com", "medium.com", "crunchbase.com", "wellfound.com", "angel.co", "tracxn.com",
    "inc42.com", "ycombinator.com", "play.google.com", "apps.apple.com", "google.com", "goo.gl",
    "bit.ly", "linktr.ee", "tinyurl.com", "t.me", "wa.me", "docsend.com", "notion.so", "notion.site",
    "substack.com", "spotify.com", "tiktok.com", "lnkd.in", "maps.app.goo.gl", "calendly.com",
)


def domain_of(url: str | None) -> str | None:
    """Registrable-ish host of a company website ("https://www.razorpay.com/x" -> "razorpay.com"),
    or None for an empty URL or a host that is never a company site."""
    if not url:
        return None
    if "//" not in url:
        url = "https://" + url
    host = urlsplit(url.strip()).netloc.lower().split("@")[-1].split(":")[0].removeprefix("www.")
    if not host or "." not in host:
        return None
    if any(host == d or host.endswith("." + d) for d in NOT_COMPANY_DOMAINS):
        return None
    return host


def clean_name(name: str | None) -> str:
    name = re.sub(r"\s+", " ", (name or "").replace("​", "")).strip(" -|·•,")
    return name[:200]


@dataclass
class Seed:
    name: str
    website: str | None = None
    is_india: bool | None = None
    india_cities: list[str] = field(default_factory=list)
    about: str | None = None
    high_priority: bool = False   # funded / accelerator-backed: resolve its ATS early
    low_priority: bool = False
    ats_token: str | None = None  # an unverified board token / ATS hint: the resolver checks it
    ats_type: str | None = None


@dataclass
class SeedContext:
    session: Session
    cache_dir: Path = DATA_DIR / "cache" / "universe"
    options: dict[str, Any] = field(default_factory=dict)   # per-seeder settings from universe.yaml


class Seeder(abc.ABC):
    name: str = ""

    def available(self) -> str | None:
        """None if the seeder can run, else the reason it is skipped (missing key, site down)."""
        return None

    @abc.abstractmethod
    def seeds(self, ctx: SeedContext) -> Iterator[Seed]:
        ...


@dataclass
class SeedStats:
    seen: int = 0
    created: int = 0
    merged: int = 0       # matched an existing company under a different spelling
    existing: int = 0     # matched an existing company under the same spelling
    skipped: int = 0      # blank / unusable name
    india_created: int = 0


def write_seed(session: Session, seed: Seed, source: str) -> str:
    """Upsert one seed. Returns 'created' | 'merged' | 'existing' | 'skipped'."""
    name = clean_name(seed.name)
    if not normalize_company(name):
        return "skipped"
    domain = domain_of(seed.website)
    company, match = None, None
    if domain:
        company = session.query(Company).filter(Company.domain == domain).first()
        match = "domain" if company else None
    if company is None:
        company = get_company(session, name)
        match = "name" if company else None
    meta = {"domain": domain, "is_india": seed.is_india, "india_cities": seed.india_cities, "about": seed.about}
    created = company is None
    if created:
        company = get_or_create_company(session, name, discovered_via=f"seed:{source}", meta=meta)
        if seed.high_priority:
            company.priority = PRIORITY_HIGH
        elif seed.low_priority:
            company.priority = PRIORITY_LOW
    else:
        _apply_meta(session, company, meta)
    if seed.ats_token and not company.ats_token:
        company.ats_token = seed.ats_token
    if seed.ats_type in ATS_TYPES and company.ats_type in (None, "unknown"):
        company.ats_type = seed.ats_type
    if domain and not company.website:
        company.website = seed.website if "//" in seed.website else "https://" + seed.website
    sources = list(company.seed_sources or [])
    if source not in sources:
        company.seed_sources = sources + [source]
    if created:
        return "created"
    if name.casefold() == company.name.casefold():
        return "existing"
    _record_alias(session, company, name, source, match, domain)
    return "merged"


def _record_alias(session: Session, company: Company, alias: str, source: str, match: str, domain: str | None) -> None:
    exists = (session.query(CompanyAlias.id)
              .filter_by(company_id=company.id, alias=alias, seed_source=source).first())
    if exists:
        return
    evidence = domain if match == "domain" else normalize_company(alias)
    session.add(CompanyAlias(company_id=company.id, alias=alias, seed_source=source, match_type=match,
                             evidence=evidence))
    log.info("company_merged", company=company.name, alias=alias, source=source, match=match, evidence=evidence)


def run_seeder(session: Session, seeder: Seeder, options: dict[str, Any] | None = None,
               commit_every: int = 200, commit_seconds: float = 5.0) -> SeedStats:
    """Commits every `commit_every` seeds or `commit_seconds`, whichever comes first: seeders that
    fetch a page per seed (inc42, portfolio details) must not hold SQLite's write lock for
    minutes, or a concurrent `discover` fails with "database is locked"."""
    stats = SeedStats()
    reason = seeder.available()
    if reason:
        log.info("seeder_skipped", seeder=seeder.name, reason=reason)
        return stats
    ctx = SeedContext(session=session, options=options or {})
    ctx.cache_dir.mkdir(parents=True, exist_ok=True)
    last_commit = time.monotonic()
    for seed in seeder.seeds(ctx):
        stats.seen += 1
        outcome = write_seed(session, seed, seeder.name)
        setattr(stats, outcome, getattr(stats, outcome) + 1)
        if outcome == "created" and seed.is_india:
            stats.india_created += 1
        if stats.seen % commit_every == 0 or time.monotonic() - last_commit >= commit_seconds:
            session.commit()
            last_commit = time.monotonic()
    session.commit()
    log.info("seeder_done", seeder=seeder.name, **stats.__dict__)
    return stats


# ── Built-in seeders ─────────────────────────────────────────────────────────

class OwnDataSeeder(Seeder):
    """Every company in ingested jobs. India when all its jobs with a known country are in India."""
    name = "own_data"

    def seeds(self, ctx: SeedContext) -> Iterator[Seed]:
        from autoapply.models.job import Job
        rows = ctx.session.query(Job.company, Job.country, Job.city).filter(Job.company.isnot(None)).all()
        countries: dict[str, set[str]] = {}
        cities: dict[str, list[str]] = {}
        for company, country, city in rows:
            countries.setdefault(company, set())
            if country:
                countries[company].add(country)
            if country == "India" and city and city not in cities.setdefault(company, []):
                cities[company].append(city)
        for company, cs in countries.items():
            yield Seed(name=company, is_india=cs == {"India"} or None, india_cities=cities.get(company, []))


class ManualSeeder(Seeder):
    """config/companies.yaml: a list of {name, website?, india?, about?, priority? (high|low),
    ats_token?, ats_type?}."""
    name = "manual"

    def __init__(self, path: Path | None = None):
        self.path = path or CONFIG_DIR / "companies.yaml"

    def available(self) -> str | None:
        return None if self.path.exists() else f"{self.path} not found"

    def seeds(self, ctx: SeedContext) -> Iterator[Seed]:
        for entry in yaml.safe_load(self.path.read_text(encoding="utf-8")) or []:
            if isinstance(entry, str):
                entry = {"name": entry}
            priority = entry.get("priority")
            yield Seed(name=entry["name"], website=entry.get("website"), is_india=entry.get("india"),
                       about=entry.get("about"), high_priority=priority in (True, "high"),
                       low_priority=priority == "low", ats_token=entry.get("ats_token"),
                       ats_type=entry.get("ats_type"))


def all_seeders() -> list[Seeder]:
    from autoapply.discovery.directories import Inc42Seeder, StartupIndiaSeeder, YCSeeder
    from autoapply.discovery.github import GitHubOrgSeeder
    from autoapply.discovery.portfolios import PortfolioSeeder
    return [OwnDataSeeder(), ManualSeeder(), PortfolioSeeder(), YCSeeder(), Inc42Seeder(),
            StartupIndiaSeeder(), GitHubOrgSeeder()]


def load_options() -> dict[str, dict[str, Any]]:
    path = CONFIG_DIR / "universe.yaml"
    return (yaml.safe_load(path.read_text(encoding="utf-8")) or {}) if path.exists() else {}


def run_universe(session: Session, only: list[str] | None = None) -> dict[str, SeedStats]:
    options = load_options()
    out = {}
    for seeder in all_seeders():
        if only and seeder.name not in only:
            continue
        opts = options.get(seeder.name) or {}
        if opts.get("enabled") is False:
            log.info("seeder_disabled", seeder=seeder.name)
            continue
        out[seeder.name] = run_seeder(session, seeder, opts)
    return out


def universe_stats(session: Session) -> dict[str, Any]:
    from autoapply.models.company import Company
    rows = session.query(Company.seed_sources, Company.is_india, Company.ats_type, Company.website).all()
    by_source: Counter = Counter()
    by_source_india: Counter = Counter()
    for sources, india, _, _ in rows:
        for s in sources or ["(none)"]:
            by_source[s] += 1
            by_source_india[s] += bool(india)
    resolved = sum(1 for _, _, ats, _ in rows if ats and ats != "unknown")
    return {
        "total": len(rows),
        "india": sum(1 for _, india, _, _ in rows if india),
        "with_website": sum(1 for *_, w in rows if w),
        "ats_resolved": resolved,
        "by_seed_source": dict(by_source.most_common()),
        "india_by_seed_source": dict(by_source_india),
        "aliases": session.query(CompanyAlias).count(),
    }
