"""Internshala (internshala.com) internships from its server-rendered category pages.

List: https://internshala.com/internships/<category-slug>/ then .../page-N/ (page-1 301s to the
bare slug). Static HTML, no JS needed; 50 cards on the first page and 40 after. The page's <h1>
starts with the category's total ("602 Computer Science Internships"). Past the last page the
site keeps serving pages, so paging stops at the first page that adds no new internship ids.

Card:  div.individual_internship[internshipid][employment_type][data-href]
       .job-internship-name, .company-name, .row-1-item (location / stipend / duration, told
       apart by their icon class), .about_job .text (snippet), .job_skill, .status-* ("2 days ago")
Detail (only for cards that pass evaluate_job; cached on disk by internshipid):
       the first .detail_view holds this posting; the page also embeds "similar internships"
       below it, so parsing is scoped to that block. .internship_details (description, skills,
       who can apply), .about_company_text_container, .apply_by ("APPLY BY 28 Oct' 26"),
       #start-date-first, #location_names.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlsplit

from bs4 import BeautifulSoup, Tag

from autoapply.logging import get_logger
from autoapply.sources.base import BaseSource, SourceResult
from autoapply.sources.http_client import HttpClient
from autoapply.sources.location import find_cities

log = get_logger(__name__)

BASE = "https://internshala.com"


def _text(el: Tag | None) -> str:
    return el.get_text(" ", strip=True) if el else ""


def _row_item(card: Tag, icon: str) -> str:
    for item in card.select(".row-1-item"):
        if item.select_one(f"i.{icon}"):
            return _text(item)
    return ""


_AGO_RE = re.compile(r"(\d+)\s*(day|week|month|hour)s?\s+ago", re.I)


def parse_ago(text: str, now: datetime | None = None) -> datetime | None:
    """'Just now' / 'Today' / '3 days ago' / '2 weeks ago' -> datetime (approximate)."""
    now = now or datetime.now(timezone.utc)
    t = (text or "").lower()
    if "just now" in t or "today" in t or "few hours" in t:
        return now
    m = _AGO_RE.search(t)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    days = {"hour": 0, "day": n, "week": 7 * n, "month": 30 * n}[unit]
    return now - timedelta(days=days, hours=n if unit == "hour" else 0)


def parse_apply_by(text: str) -> datetime | None:
    m = re.search(r"(\d{1,2}\s+\w{3})'\s*(\d{2})", text or "")
    if not m:
        return None
    try:
        return datetime.strptime(f"{m.group(1)} {m.group(2)}", "%d %b %y").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _work_mode(location: str) -> str:
    t = location.lower()
    if "work from home" in t:
        return "remote"
    if "hybrid" in t:
        return "hybrid"
    return "onsite" if t else "unknown"


def parse_listing(html: str) -> tuple[list[SourceResult], int | None]:
    """Cards on one listing page, and the category total from the <h1> (None if absent)."""
    soup = BeautifulSoup(html, "lxml")
    h1 = re.match(r"\s*([\d,]+)", _text(soup.find("h1")))
    total = int(h1.group(1).replace(",", "")) if h1 else None
    return [r for r in (parse_card(c) for c in soup.select("div.individual_internship[internshipid]")) if r], total


def parse_card(card: Tag) -> SourceResult | None:
    iid = card.get("internshipid")
    title = _text(card.select_one(".job-internship-name"))
    if not iid or not title:
        return None
    url = BASE + card.get("data-href", f"/internship/detail/{iid}")
    location = _row_item(card, "ic-16-map-pin") or _row_item(card, "ic-16-home")
    stipend = _row_item(card, "ic-16-money")
    duration = _row_item(card, "ic-16-calendar")
    snippet = _text(card.select_one(".about_job .text"))
    skills = [_text(s) for s in card.select(".job_skill")]
    posted = next((_text(s) for s in card.select(".color-labels span") if "ago" in _text(s).lower()
                   or "now" in _text(s).lower() or "today" in _text(s).lower()), "")
    mode = _work_mode(location)
    lines = [snippet, f"Skills: {', '.join(skills)}" if skills else "", f"Duration: {duration}" if duration else ""]
    return SourceResult(
        title=title,
        company=_text(card.select_one(".company-name")) or None,
        source="internshala",
        source_id=str(iid),
        source_url=url,
        application_url=url,
        location="Remote" if mode == "remote" else location or None,
        work_mode=mode,
        description_text="\n".join(l for l in lines if l) or None,
        compensation_text=stipend or None,
        posted_date=parse_ago(posted),
        employment_type=card.get("employment_type") or "internship",
        tags=skills or None,
        raw_data={"internship_id": iid, "location": location, "stipend": stipend, "duration": duration,
                  "posted": posted, "skills": skills},
        company_meta={"is_india": True, "india_cities": list(find_cities(location)) if mode != "remote" else []},
    )


def parse_detail(html: str) -> dict[str, Any]:
    """Fields from a detail page, scoped to the posting itself (not the similar-internships list)."""
    soup = BeautifulSoup(html, "lxml")
    view = soup.select_one(".detail_view") or soup
    details = view.select_one(".internship_details") or soup.select_one(".internship_details")
    about = soup.select_one(".about_company_text_container")
    website = None
    if about is not None:
        for a in about.parent.select("a[href^=http]"):
            host = urlsplit(a["href"]).netloc.lower()
            if host and "internshala" not in host:
                website = a["href"]
                break
    return {
        "description_raw": str(details) if details else None,
        "description_text": details.get_text("\n", strip=True) if details else None,
        "about": _text(about) or None,
        "website": website,
        "apply_by": _text(view.select_one(".apply_by")),
        "start_date": _text(view.select_one("#start-date-first")),
        "locations": _text(view.select_one("#location_names")),
    }


class InternshalaSource(BaseSource):
    """`search_cfg` is the SearchConfig used to decide which cards earn a detail fetch."""

    def __init__(self, cfg: Any = None, search_cfg: Any = None, http: HttpClient | None = None,
                 cache_dir: Path | None = None):
        from autoapply.config import DATA_DIR, InternshalaSearch
        self.cfg = cfg or InternshalaSearch()
        self.search_cfg = search_cfg
        self._http = http
        self.cache_dir = cache_dir or DATA_DIR / "cache" / "internshala"
        self.stats = {"cards": 0, "details_fetched": 0, "details_cached": 0, "details_skipped": 0}

    @property
    def name(self) -> str:
        return "internshala"

    def _detail(self, client: HttpClient, result: SourceResult) -> dict[str, Any] | None:
        path = self.cache_dir / f"{result.source_id}.json"
        if path.exists():
            self.stats["details_cached"] += 1
            return json.loads(path.read_text(encoding="utf-8"))
        resp = client.get(result.source_url)
        if resp is None or resp.status_code != 200:
            return None
        detail = parse_detail(resp.text)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(detail, ensure_ascii=False), encoding="utf-8")
        self.stats["details_fetched"] += 1
        return detail

    def _passes_filter(self, result: SourceResult) -> bool:
        from autoapply.sources.filter import evaluate_job
        return evaluate_job(result.to_dict(), self.search_cfg)["classification_status"] != "AUTO_REJECT"

    @staticmethod
    def _enrich(result: SourceResult, detail: dict[str, Any]) -> None:
        card_text = result.description_text
        if detail.get("description_text"):
            result.description_raw = detail["description_raw"]
            result.description_text = detail["description_text"] + (f"\n{card_text}" if card_text else "")
        result.deadline = parse_apply_by(detail.get("apply_by") or "") or result.deadline
        meta = dict(result.company_meta or {})
        meta["about"] = detail.get("about")
        if detail.get("website"):
            meta["domain"] = urlsplit(detail["website"]).netloc
        result.company_meta = meta
        result.company_info = detail.get("about")
        result.raw_data = {**(result.raw_data or {}), "start_date": detail.get("start_date"),
                           "detail_locations": detail.get("locations")}

    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        client = self._http or HttpClient(requests_per_second=2.0,
                                          headers={"X-Requested-With": "XMLHttpRequest"})
        seen: set[str] = set()
        try:
            for slug in self.cfg.categories:
                in_category: set[str] = set()  # paging stops when the category itself repeats
                for page in range(1, self.cfg.max_pages + 1):
                    url = f"{BASE}/internships/{slug}/" + (f"page-{page}/" if page > 1 else "")
                    resp = client.get(url)
                    if resp is None or resp.status_code != 200:
                        log.warning("internshala_page_failed", url=url, status=getattr(resp, "status_code", None))
                        break
                    results, total = parse_listing(resp.text)
                    fresh = {r.source_id for r in results} - in_category
                    in_category |= fresh
                    new = [r for r in results if r.source_id not in seen]
                    seen.update(r.source_id for r in new)
                    log.info("internshala_page", slug=slug, page=page, cards=len(results), new=len(new), total=total)
                    for result in new:
                        self.stats["cards"] += 1
                        if self.cfg.fetch_details and self._passes_filter(result):
                            detail = self._detail(client, result)
                            if detail:
                                self._enrich(result, detail)
                        else:
                            self.stats["details_skipped"] += 1
                        yield result
                    if not fresh or (total is not None and len(in_category) >= total):
                        break
        finally:
            if self._http is None:
                client.close()
            log.info("internshala_done", **self.stats)
