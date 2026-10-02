"""Company brief: what a company does, product, stage, size, location, and notable lines from its own
site. Fetched once per company and cached permanently on companies.company_brief; shared by answer
tier 3 (the only company facts a generated answer may use) and interview prep. Fetch once, use twice.

Extracted, never generated: every value is either a database fact (stage, funding, cities, the
about text a job source supplied) or text lifted verbatim from the company's own pages (title, meta
/ og description, schema.org Organization JSON-LD, the first paragraphs of the homepage and its
about page), each with the URL it came from. No model writes any of it, so tier 3's verification
pass can check generated claims against it.

Fetching: the company website (or https://<domain>), then one about page linked from it. robots.txt
is read first per host (RobotsRules, RFC 9309) and a disallowed or unreadable one means no fetch.
Requests go through the `company_brief` politeness budget (config/politeness.yaml) and are logged
as a source_runs row so the daily cap holds across runs. A company with no website gets a brief
from database facts only. A failed fetch is cached too (fetch_error): "once" means once; pass
refresh=True (CLI --refresh) to fetch again.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from autoapply.logging import get_logger
from autoapply.models.company import Company

log = get_logger(__name__)

SOURCE = "company_brief"
ABOUT_LINK = re.compile(r"\babout\b|who we are|our story|company", re.I)
MAX_PARAGRAPHS = 6


def _clean(text: str | None, limit: int = 600) -> str | None:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text[:limit] or None


def _jsonld_org(soup: BeautifulSoup) -> dict[str, Any]:
    for tag in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(tag.string or "")
        except (ValueError, TypeError):
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for it in items:
            if isinstance(it, dict) and str(it.get("@type", "")).lower() in ("organization", "corporation", "localbusiness"):
                return it
    return {}


def extract(html: str, url: str) -> dict[str, Any]:
    """Facts lifted from one page, each value verbatim from it."""
    soup = BeautifulSoup(html, "lxml")
    meta = lambda **kw: (soup.find("meta", attrs=kw) or {}).get("content")  # noqa: E731
    org = _jsonld_org(soup)
    for t in soup(["script", "style", "noscript", "nav", "footer", "header", "form"]):
        t.decompose()
    paras = [p for p in (_clean(x.get_text(" "), 400) for x in soup.find_all(["p", "h1", "h2"])) if p and len(p) > 40]
    size = org.get("numberOfEmployees")
    if isinstance(size, dict):
        size = size.get("value") or "-".join(str(size[k]) for k in ("minValue", "maxValue") if size.get(k))
    addr = org.get("address") or {}
    if isinstance(addr, list):
        addr = addr[0] if addr else {}
    location = ", ".join(str(addr[k]) for k in ("addressLocality", "addressRegion", "addressCountry")
                         if isinstance(addr, dict) and addr.get(k) and isinstance(addr[k], str))
    return {"url": url, "title": _clean(soup.title.get_text() if soup.title else None, 200),
            "description": _clean(meta(name="description") or meta(property="og:description") or org.get("description")),
            "founded": org.get("foundingDate"), "size": str(size) if size else None, "location": location or None,
            "paragraphs": list(dict.fromkeys(paras))[:MAX_PARAGRAPHS]}


def _about_url(html: str, base: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    site = urlsplit(base).netloc
    for a in soup.find_all("a", href=True):
        href = urljoin(base, a["href"])
        if urlsplit(href).netloc == site and ABOUT_LINK.search(a.get_text(" ") + " " + a["href"]) \
                and not re.search(r"career|job|login|blog/", href, re.I):
            return href.split("#")[0]
    return None


NOT_COMPANY_HOSTS = re.compile(r"(internshala|unstop|naukri|himalayas|linkedin|wellfound|instahyre|indeed|glassdoor"
                               r"|adzuna|google|haire)\.", re.I)


def own_site(session: Session, c: Company) -> str | None:
    """The company's website, else the host of its careers page or of its jobs' apply links, when
    that host is the company's own (not an ATS, not a job board)."""
    from autoapply.ats.patterns import find_ats_refs
    from autoapply.models.job import Job
    if c.website or c.domain:
        return c.website or f"https://{c.domain}"
    urls = [c.careers_url] + [u for row in session.query(Job.application_url, Job.source_url)
                              .filter(Job.company_id == c.id).limit(20) for u in row]
    for u in urls:
        host = urlsplit(u or "").netloc.lower()
        if host and not find_ats_refs(u) and not NOT_COMPANY_HOSTS.search(host + "."):
            return f"https://{host}"
    return None


def db_facts(session: Session, c: Company) -> dict[str, Any]:
    from autoapply.models.job import Job
    info = next((r[0] for r in session.query(Job.company_info).filter(Job.company_id == c.id,
                                                                     Job.company_info.isnot(None)).limit(1)), None)
    return {k: v for k, v in {
        "name": c.name, "stage": c.stage, "funding": c.funding, "india": bool(c.is_india) or None,
        "cities": c.india_cities or None, "about": _clean(c.about_text or info, 1500),
        "website": own_site(session, c)}.items() if v not in (None, "", [])}


def build(session: Session, c: Company, fetch_text: Callable[[str], str | None] | None) -> dict[str, Any]:
    from autoapply.appliers.resolve import Robots
    brief: dict[str, Any] = {"db": db_facts(session, c), "pages": [], "fetched_at": datetime.now(timezone.utc).isoformat()}
    home = brief["db"].get("website")
    if not home or fetch_text is None:
        brief["fetch_error"] = "no website known" if not home else "fetch disabled"
        return brief
    robots = Robots(fetch_text)
    queue = [home]
    while queue and len(brief["pages"]) < 2:
        url = queue.pop(0)
        allowed = robots.allowed(url)
        if allowed is not True:
            brief.setdefault("skipped", []).append(f"{url}: robots.txt {'disallows' if allowed is False else 'unreadable'}")
            continue
        html = fetch_text(url)
        if not html:
            brief.setdefault("skipped", []).append(f"{url}: no response")
            continue
        brief["pages"].append(extract(html, url))
        if len(brief["pages"]) == 1:
            about = _about_url(html, url)
            if about and about != url:
                queue.append(about)
    if not brief["pages"]:
        brief["fetch_error"] = "; ".join(brief.get("skipped", [])) or "nothing fetched"
    return brief


def summary(brief: dict[str, Any] | None) -> dict[str, Any]:
    """The five headline fields (what / product / stage / size / location) and notable lines, from a brief."""
    if not brief:
        return {}
    db, pages = brief.get("db") or {}, brief.get("pages") or []
    first = lambda k: next((p[k] for p in pages if p.get(k)), None)  # noqa: E731
    about = db.get("about") if len(db.get("about") or "") >= 60 else None    # seeder stubs ("Portfolio: X") are not a description
    return {"name": db.get("name"), "what": first("description") or about, "product": first("title"),
            "stage": " / ".join(x for x in (db.get("stage"), db.get("funding")) if x) or None,
            "size": first("size"), "founded": first("founded"),
            "location": first("location") or (", ".join(db["cities"]) if db.get("cities") else None),
            "notable": [p for page in pages for p in page.get("paragraphs", [])][:6],
            "sources": [p["url"] for p in pages], "fetch_error": brief.get("fetch_error")}


def passages(brief: dict[str, Any] | None) -> list[str]:
    """The brief as text passages: what tier 3 may cite about the company."""
    s = summary(brief)
    out = [f"{k}: {s[k]}" for k in ("name", "what", "product", "stage", "size", "founded", "location") if s.get(k)]
    return out + list(s.get("notable") or [])


def _http_fetcher(session: Session):
    from autoapply.models.source_run import OK, SourceRun
    from autoapply.politeness import Budget, Pacer, policy_for
    from autoapply.sources.http_client import HttpClient
    policy = policy_for(SOURCE)
    budget, pacer = Budget.for_source(session, SOURCE, policy), Pacer(policy)
    run = SourceRun(source=SOURCE, tier="http", status=OK)
    session.add(run)
    http = HttpClient(requests_per_second=1.0, max_retries=1, timeout=20)

    def fetch(url: str) -> str | None:
        budget.take()
        pacer.wait()
        run.requests = (run.requests or 0) + 1
        resp = http.get(url)
        if resp is None or resp.status_code != 200 or "html" not in resp.headers.get("content-type", "text/html") \
                and not url.endswith("robots.txt"):
            return None
        return resp.text
    return fetch, run, http


def brief_for(session: Session, company: Company | None, *, fetch: bool = True, refresh: bool = False,
              fetch_text: Callable[[str], str | None] | None = None) -> dict[str, Any] | None:
    """The cached brief, or build it now (once). fetch=False never makes a request (and caches nothing)."""
    if company is None or company.id is None:
        return None
    if company.company_brief and not refresh:
        return company.company_brief
    if not fetch:
        return None
    own = fetch_text is None
    run = http = None
    if own:
        fetch_text, run, http = _http_fetcher(session)
    try:
        brief = build(session, company, fetch_text)
    except Exception as e:     # BudgetExhausted included: try again another day, cache nothing
        log.warning("company_brief_failed", company=company.name, error=str(e)[:200])
        return None
    finally:
        if http is not None:
            http.close()
        if run is not None:
            run.finished_at = datetime.now(timezone.utc)
    company.company_brief, company.company_brief_at = brief, datetime.now(timezone.utc)
    session.commit()
    log.info("company_brief_cached", company=company.name, pages=len(brief["pages"]), error=brief.get("fetch_error"))
    return brief


def company_of(session: Session, job) -> Company | None:
    if job.company_id:
        return session.get(Company, job.company_id)
    from autoapply.services.dedup import normalize_company
    return session.query(Company).filter_by(normalized_name=normalize_company(job.company)).first()
