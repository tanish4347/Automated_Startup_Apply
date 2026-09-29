"""Generic VC-portfolio scraper, configured by config/portfolios.yaml.

Portfolio pages come in a few shapes (surveyed 2026-09-30), each an extraction strategy:
  jsonld    schema.org Organization entries in <script type="application/ld+json"> (Stellaris)
  internal  cards linking to the VC's own /companies/<slug> or /portfolio/<slug> pages, with the
            name in the card (Peak XV, Elevation, Z47, Kalaari, 3one4, Nexus, Surge, Blume)
  links     a grid of outbound links straight to company websites (India Quotient, Better
            Capital, Antler, Prime)
  regex     data embedded in scripts (Accel's Sanity CMS objects: name ... websiteUrl)
  auto      jsonld, else internal, else links: whichever finds the most entries

For `internal` entries the website usually lives on the detail page; with follow_details the
scraper opens each one (capped by max_details) and takes the outbound link labelled as the
website, else the first outbound non-social link. That website is what the ATS resolver needs.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from typing import Any, Iterator, Literal
from urllib.parse import urljoin, urlsplit

import yaml
from bs4 import BeautifulSoup, Tag
from pydantic import BaseModel

from autoapply.discovery.universe import CONFIG_DIR, Seed, SeedContext, Seeder, clean_name, domain_of
from autoapply.logging import get_logger
from autoapply.sources.http_client import HttpClient

log = get_logger(__name__)

DEFAULT_INTERNAL = r"^/(?:companies|company|portfolio|portfolio-companies|startups|investments)/[^/?#]+/?$"


class PortfolioSpec(BaseModel):
    name: str
    url: str
    india: bool = True                 # an India-focused fund: its companies are marked is_india
    strategy: Literal["auto", "jsonld", "internal", "links", "regex"] = "auto"
    internal_pattern: str = DEFAULT_INTERNAL
    # Where the name is, inside the card/anchor: "css", "css@attr", "@attr" (on the anchor itself),
    # or "slug" (last path segment of the link). Default: aria-label/title, text, then img alt.
    name_selector: str | None = None
    name_regex: str | None = None      # applied to the label; group 1 is the name
    regex: str | None = None           # named groups: name (required), website / detail (optional)
    follow_details: bool = True
    max_details: int = 400
    exclude_names: list[str] = []
    exclude_domains: list[str] = []
    enabled: bool = True
    note: str | None = None


def load_specs(path=None) -> list[PortfolioSpec]:
    raw = yaml.safe_load((path or CONFIG_DIR / "portfolios.yaml").read_text(encoding="utf-8")) or []
    return [PortfolioSpec.model_validate(x) for x in raw]


# ── strategies ───────────────────────────────────────────────────────────────

def _site(url: str) -> str:
    return urlsplit(url).netloc.lower().removeprefix("www.")


def _external(href: str, base: str, spec: PortfolioSpec) -> str | None:
    """Domain of an outbound company link, or None (same site, social, excluded)."""
    url = urljoin(base, href)
    d = domain_of(url)
    site = _site(base)
    root = ".".join(site.split(".")[-2:])
    if not d or d == site or d.endswith("." + root) or root in d:
        return None
    if any(d == x or d.endswith("." + x) for x in spec.exclude_domains):
        return None
    return d


def _label(el: Tag, selector: str | None, name_regex: str | None = None) -> str:
    label = _raw_label(el, selector)
    if name_regex:
        m = re.search(name_regex, label)
        label = m.group(1) if m else ""
    # A label that is just a URL ("https://www.aroleap.com/") names nothing; the caller falls
    # back to the domain or slug.
    return "" if re.match(r"^(https?://|www\.)", label.strip(), re.I) else label


def _raw_label(el: Tag, selector: str | None) -> str:
    if selector == "slug":
        return _slug_name(el.get("href", ""))
    if selector:
        css, _, attr = selector.partition("@")
        found = el.select_one(css) if css else el
        if found is not None:
            return (found.get(attr) or "") if attr else found.get_text(" ", strip=True)
        return ""
    for attr in ("aria-label", "title"):
        if el.get(attr):
            return el[attr]
    text = el.get_text("\n", strip=True).split("\n")[0] if el.get_text(strip=True) else ""
    if text:
        return text
    img = el.find("img")
    if img is not None and img.get("alt"):
        return re.sub(r"\s*(logo|image|icon)\s*$", "", img["alt"], flags=re.I)
    return ""


def _slug_name(url: str) -> str:
    slug = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1]
    return slug.replace("-", " ").replace("_", " ").title()


def extract_jsonld(html: str, base: str) -> list[dict[str, Any]]:
    out = []
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', html, re.S):
        try:
            data = json.loads(block)
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                if node.get("@type") in ("Organization", "Corporation") and node.get("name"):
                    url = node.get("url") or node.get("sameAs")
                    url = url[0] if isinstance(url, list) and url else url
                    out.append({"name": node["name"], "url": urljoin(base, url) if isinstance(url, str) else None})
                stack.extend(v for v in node.values() if isinstance(v, (list, dict)))
    site = _site(base)
    # The VC's own Organization block describes the VC itself.
    return [x for x in out if not (x["url"] and _site(x["url"]) == site and urlsplit(x["url"]).path in ("", "/"))]


def extract_internal(soup: BeautifulSoup, base: str, spec: PortfolioSpec) -> list[dict[str, Any]]:
    pattern = re.compile(spec.internal_pattern)
    listing_path = urlsplit(base).path.rstrip("/")
    found: dict[str, dict[str, Any]] = {}
    for a in soup.find_all("a", href=True):
        url = urljoin(base, a["href"])
        if _site(url) != _site(base):
            continue
        path = urlsplit(url).path
        if not pattern.match(path) or path.rstrip("/") == listing_path:
            continue
        name = _label(a, spec.name_selector, spec.name_regex)
        if url not in found or (name and not found[url]["name"]):
            found[url] = {"name": name, "detail": url}
    for v in found.values():
        v["name"] = v["name"] or _slug_name(v["detail"])
    return list(found.values())


def extract_links(soup: BeautifulSoup, base: str, spec: PortfolioSpec) -> list[dict[str, Any]]:
    found: dict[str, dict[str, Any]] = {}
    for a in soup.find_all("a", href=True):
        d = _external(a["href"], base, spec)
        if not d:
            continue
        name = _label(a, spec.name_selector, spec.name_regex)
        if d not in found or (name and not found[d]["name"]):
            found[d] = {"name": name, "url": urljoin(base, a["href"])}
    for d, v in found.items():
        v["name"] = v["name"] or d.split(".")[0].title()
    return list(found.values())


def extract_regex(html: str, base: str, spec: PortfolioSpec) -> list[dict[str, Any]]:
    text = html.replace('\\"', '"').replace("\\/", "/")
    out, seen = [], set()
    for m in re.finditer(spec.regex, text, re.S):
        groups = m.groupdict()
        name = htmllib.unescape(groups["name"])
        if re.fullmatch(r"[a-z0-9-]+", name):  # a slug
            name = name.replace("-", " ").title()
        if name in seen:
            continue
        seen.add(name)
        entry = {"name": name, "url": groups.get("website") or None}
        if groups.get("detail"):
            entry["detail"] = urljoin(base, groups["detail"])
        out.append(entry)
    return out


def extract(html: str, base: str, spec: PortfolioSpec) -> list[dict[str, Any]]:
    """Entries on a portfolio page: dicts with name and url (website) or detail (VC page)."""
    if spec.strategy == "regex":
        return extract_regex(html, base, spec)
    soup = BeautifulSoup(html, "lxml")
    options = {
        "jsonld": lambda: extract_jsonld(html, base),
        "internal": lambda: extract_internal(soup, base, spec),
        "links": lambda: extract_links(soup, base, spec),
    }
    if spec.strategy != "auto":
        entries = options[spec.strategy]()
    else:
        entries = []
        for key in ("jsonld", "internal", "links"):
            got = options[key]()
            if len(got) >= 8:
                entries = got
                break
            entries = max(entries, got, key=len)
    # A jsonld entry pointing at the VC's own detail page is really an internal entry.
    for e in entries:
        if e.get("url") and _site(e["url"]) == _site(base):
            e["detail"], e["url"] = e.pop("url"), None
    skip = {n.casefold() for n in spec.exclude_names}
    return [e for e in entries if clean_name(e["name"]) and clean_name(e["name"]).casefold() not in skip]


def website_from_detail(html: str, base: str, spec: PortfolioSpec) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    main = soup.find("main") or soup.body or soup
    for tag in main.find_all(["header", "footer", "nav"]):
        tag.decompose()
    candidates = [(a, _external(a["href"], base, spec)) for a in main.find_all("a", href=True)]
    candidates = [(a, d) for a, d in candidates if d]
    for a, d in candidates:
        label = a.get_text(" ", strip=True).lower()
        if "website" in label or "visit" in label or label.removeprefix("www.").startswith(d.split(".")[0]):
            return urljoin(base, a["href"])
    return urljoin(base, candidates[0][0]["href"]) if candidates else None


# ── seeder ───────────────────────────────────────────────────────────────────

class PortfolioSeeder(Seeder):
    name = "portfolios"

    def __init__(self, specs: list[PortfolioSpec] | None = None, http: HttpClient | None = None):
        self._specs = specs
        self._http = http

    def available(self) -> str | None:
        return None if self._specs is not None or (CONFIG_DIR / "portfolios.yaml").exists() else "no portfolios.yaml"

    def seeds(self, ctx: SeedContext) -> Iterator[Seed]:
        specs = self._specs if self._specs is not None else load_specs()
        only = set(ctx.options.get("only") or [])
        client = self._http or HttpClient(requests_per_second=2.0)
        try:
            for spec in specs:
                if not spec.enabled or (only and spec.name not in only):
                    continue
                yield from self._one(client, spec)
        finally:
            if self._http is None:
                client.close()

    def _one(self, client: HttpClient, spec: PortfolioSpec) -> Iterator[Seed]:
        resp = client.get(spec.url)
        if resp is None or resp.status_code != 200:
            log.warning("portfolio_fetch_failed", vc=spec.name, url=spec.url, status=getattr(resp, "status_code", None))
            return
        base = str(resp.url)
        entries = extract(resp.text, base, spec)
        details, failures_in_a_row = 0, 0
        for e in entries:
            website = e.get("url")
            follow = spec.follow_details and details < spec.max_details and failures_in_a_row < 3
            if not website and e.get("detail") and follow:
                details += 1
                page = client.get(e["detail"])
                if page is not None and page.status_code == 200:
                    failures_in_a_row = 0
                    website = website_from_detail(page.text, str(page.url), spec)
                else:  # rate-limited or down: after 3 in a row, keep the names, skip the websites
                    failures_in_a_row += 1
                    if failures_in_a_row == 3:
                        log.warning("portfolio_details_stopped", vc=spec.name, after=details)
            yield Seed(name=e["name"], website=website, is_india=True if spec.india else None,
                       about=f"Portfolio: {spec.name}", high_priority=True)
        log.info("portfolio_done", vc=spec.name, entries=len(entries), details_fetched=details,
                 with_website=sum(1 for e in entries if e.get("url")))
