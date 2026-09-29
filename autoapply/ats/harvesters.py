"""One harvester per ATS. Every endpoint here was verified live against a real company board
(see docs/ATS.md); none are guessed.

Each harvester takes (fetcher, target) and returns SourceResults with full descriptions where
the ATS provides them. When a board lists jobs without descriptions and is large, details are
fetched only for titles that could be internships (DETAIL_HINT_RE), to keep a 2,000-job board
from costing 2,000 requests. Every job is still returned, with or without a description.
"""

from __future__ import annotations

import html as htmllib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import quote

from autoapply.ats.http import AsyncFetcher
from autoapply.logging import get_logger
from autoapply.sources.base import SourceResult
from autoapply.sources.http_client import html_to_text

log = get_logger(__name__)

# Boards with at most this many jobs get every description fetched.
FULL_DETAIL_LIMIT = 60
DETAIL_HINT_RE = re.compile(
    r"\b(intern|interns|internship|trainee|apprentice|graduate|fresher|freshers|co-?op|working student"
    r"|student|summer|campus|new grad)\b", re.I)


@dataclass(frozen=True)
class Target:
    """What a harvester needs to know about a company."""
    company: str
    ats_type: str
    token: str
    careers_url: str | None = None


class HarvestError(Exception):
    """The board could not be read (distinct from 'read fine, zero jobs')."""


def text_of(html_str: str | None) -> str:
    return html_to_text(htmllib.unescape(html_str or "")) if html_str else ""


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value, tz=timezone.utc)
        s = str(value).strip().replace("Z", "+00:00")
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
            s += "T00:00:00+00:00"
        dt = datetime.fromisoformat(s.replace(" UTC", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _mode(remote: Any = None, hybrid: Any = None, onsite: Any = None, workplace: str | None = None) -> str:
    w = (workplace or "").lower().replace("-", "").replace("_", "")
    if w in ("remote",):
        return "remote"
    if w in ("hybrid",):
        return "hybrid"
    if w in ("onsite", "inoffice", "office"):
        return "onsite"
    if remote is True:
        return "remote"
    if hybrid is True:
        return "hybrid"
    if onsite is True:
        return "onsite"
    return "unknown"


def _needs_detail(title: str, board_size: int) -> bool:
    return board_size <= FULL_DETAIL_LIMIT or bool(DETAIL_HINT_RE.search(title or ""))


def _result(t: Target, **kw: Any) -> SourceResult:
    return SourceResult(company=t.company, source=t.ats_type, ats_platform=t.ats_type, **kw)


# ── Greenhouse ─────────────────────────────────────────────────────────────

async def greenhouse(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    data = await f.get_json(f"https://boards-api.greenhouse.io/v1/boards/{t.token}/jobs", params={"content": "true"})
    if data is None:
        raise HarvestError("greenhouse board not readable")
    out = []
    for j in data.get("jobs", []):
        content = htmllib.unescape(j.get("content") or "")
        out.append(_result(
            t, title=j.get("title", ""), source_id=str(j.get("id")), source_url=j.get("absolute_url"),
            application_url=j.get("absolute_url"), location=(j.get("location") or {}).get("name"),
            description_raw=content, description_text=html_to_text(content),
            posted_date=parse_dt(j.get("first_published") or j.get("updated_at")),
        ))
    return out


# ── Lever ──────────────────────────────────────────────────────────────────

async def lever(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    data = await f.get_json(f"https://api.lever.co/v0/postings/{t.token}", params={"mode": "json"})
    if data is None:
        data = await f.get_json(f"https://api.eu.lever.co/v0/postings/{t.token}", params={"mode": "json"})
    if not isinstance(data, list):
        raise HarvestError("lever board not readable")
    out = []
    for j in data:
        cats = j.get("categories") or {}
        parts = [j.get("descriptionPlain") or ""]
        for lst in j.get("lists") or []:
            parts.append(f"{lst.get('text', '')}\n{text_of(lst.get('content'))}")
        parts.append(j.get("additionalPlain") or "")
        out.append(_result(
            t, title=j.get("text", ""), source_id=j.get("id"), source_url=j.get("hostedUrl"),
            application_url=j.get("applyUrl") or j.get("hostedUrl"),
            location=cats.get("location") or ", ".join(cats.get("allLocations") or []),
            work_mode=_mode(workplace=j.get("workplaceType")), employment_type=cats.get("commitment"),
            description_raw=j.get("description"), description_text="\n\n".join(p for p in parts if p).strip(),
            compensation_text=(j.get("salaryDescriptionPlain") or None),
            posted_date=parse_dt(j.get("createdAt")),
        ))
    return out


# ── Ashby ──────────────────────────────────────────────────────────────────

async def ashby(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    data = await f.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{t.token}",
                            params={"includeCompensation": "true"})
    if data is None:
        raise HarvestError("ashby board not readable")
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        comp = (j.get("compensation") or {}).get("compensationTierSummary")
        locs = [j.get("location")] + [s.get("location") for s in j.get("secondaryLocations") or []]
        out.append(_result(
            t, title=j.get("title", ""), source_id=j.get("id"), source_url=j.get("jobUrl"),
            application_url=j.get("applyUrl") or j.get("jobUrl"), location=" / ".join(l for l in locs if l),
            work_mode=_mode(remote=j.get("isRemote"), workplace=j.get("workplaceType")),
            employment_type=j.get("employmentType"), description_raw=j.get("descriptionHtml"),
            description_text=j.get("descriptionPlain") or text_of(j.get("descriptionHtml")),
            compensation_text=comp, posted_date=parse_dt(j.get("publishedAt")),
        ))
    return out


# ── Workable ───────────────────────────────────────────────────────────────

async def workable(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    jobs: list[dict] = []
    body: dict[str, Any] = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
    for _ in range(50):  # pages
        data = await f.post_json(f"https://apply.workable.com/api/v3/accounts/{t.token}/jobs", json=body)
        if data is None:
            if not jobs:
                raise HarvestError("workable board not readable")
            break
        jobs.extend(data.get("results") or [])
        if not data.get("nextPage"):
            break
        body["token"] = data["nextPage"]
    out = []
    for j in jobs:
        loc = j.get("location") or {}
        url = f"https://apply.workable.com/{t.token}/j/{j.get('shortcode')}/"
        detail = None
        if _needs_detail(j.get("title", ""), len(jobs)):
            detail = await f.get_json(f"https://apply.workable.com/api/v2/accounts/{t.token}/jobs/{j.get('shortcode')}")
        desc_html = "\n".join((detail or {}).get(k) or "" for k in ("description", "requirements", "benefits"))
        out.append(_result(
            t, title=j.get("title", ""), source_id=j.get("shortcode"), source_url=url, application_url=url,
            location=", ".join(x for x in (loc.get("city"), loc.get("region"), loc.get("country")) if x),
            work_mode=_mode(remote=j.get("remote"), workplace=j.get("workplace")),
            employment_type=j.get("type"), description_raw=desc_html or None,
            description_text=text_of(desc_html) or None, posted_date=parse_dt(j.get("published")),
        ))
    return out


# ── SmartRecruiters ────────────────────────────────────────────────────────

async def smartrecruiters(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    base = f"https://api.smartrecruiters.com/v1/companies/{t.token}/postings"
    jobs: list[dict] = []
    offset = 0
    while True:
        data = await f.get_json(base, params={"limit": 100, "offset": offset})
        if data is None:
            if not jobs:
                raise HarvestError("smartrecruiters board not readable")
            break
        page = data.get("content") or []
        jobs.extend(page)
        offset += len(page)
        if not page or offset >= (data.get("totalFound") or 0):
            break
    out = []
    for j in jobs:
        loc = j.get("location") or {}
        url = f"https://jobs.smartrecruiters.com/{t.token}/{j.get('id')}"
        desc = None
        if _needs_detail(j.get("name", ""), len(jobs)):
            d = await f.get_json(f"{base}/{j.get('id')}")
            if d:
                url = d.get("postingUrl") or url
                sections = (d.get("jobAd") or {}).get("sections") or {}
                desc = "\n".join(f"{s.get('title', '')}\n{s.get('text', '')}" for s in sections.values() if s)
        out.append(_result(
            t, title=j.get("name", ""), source_id=str(j.get("id")), source_url=url, application_url=url,
            location=loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("country")) if x),
            work_mode=_mode(remote=loc.get("remote"), hybrid=loc.get("hybrid")),
            employment_type=(j.get("typeOfEmployment") or {}).get("label"),
            description_raw=desc, description_text=text_of(desc) or None,
            posted_date=parse_dt(j.get("releasedDate")),
        ))
    return out


# ── Recruitee ──────────────────────────────────────────────────────────────

async def recruitee(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    data = await f.get_json(f"https://{t.token}.recruitee.com/api/offers/")
    if data is None:
        raise HarvestError("recruitee board not readable")
    out = []
    for j in data.get("offers", []):
        sal = j.get("salary") or {}
        comp = None
        if sal.get("min") or sal.get("max"):
            comp = f"{sal.get('currency') or ''} {sal.get('min') or ''} - {sal.get('max') or ''} per {sal.get('period') or ''}"
        desc_html = f"{j.get('description') or ''}\n{j.get('requirements') or ''}"
        out.append(_result(
            t, title=j.get("title", ""), source_id=str(j.get("id")), source_url=j.get("careers_url"),
            application_url=j.get("careers_apply_url") or j.get("careers_url"), location=j.get("location"),
            work_mode=_mode(remote=j.get("remote"), hybrid=j.get("hybrid"), onsite=j.get("on_site")),
            employment_type=j.get("employment_type_code"), description_raw=desc_html,
            description_text=text_of(desc_html), compensation_text=comp,
            posted_date=parse_dt(j.get("published_at")),
        ))
    return out


# ── Keka ───────────────────────────────────────────────────────────────────

_KEKA_GUID_RE = re.compile(r"ats/documents/([0-9a-f-]{36})/careerportal|embedjobs/js/([0-9a-f-]{36})", re.I)


async def keka(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    base = f"https://{t.token}.keka.com/careers/"
    page = await f.get_text(base)
    m = _KEKA_GUID_RE.search(page or "")
    if not m:
        raise HarvestError("keka careers page has no portal identifier")
    guid = m.group(1) or m.group(2)
    data = await f.get_json(f"{base}api/embedjobs/default/active/{guid}")
    if not isinstance(data, list):
        raise HarvestError("keka jobs endpoint not readable")
    out = []
    for j in data:
        sal = j.get("salaryRange") or {}
        comp = None
        if sal.get("minimum") or sal.get("maximum"):
            # salaryPeriod 3 is monthly on every intern posting checked; other codes are left unlabelled.
            period = " /month" if sal.get("salaryPeriod") == 3 else ""
            comp = f"{sal.get('currency') or 'INR'} {sal.get('minimum') or ''} - {sal.get('maximum') or ''}{period}"
        locs = j.get("jobLocations") or []
        url = f"{base}jobdetails/{j.get('id')}"
        out.append(_result(
            t, title=j.get("title", ""), source_id=str(j.get("id")), source_url=url, application_url=url,
            location=" / ".join(l.get("name") or "" for l in locs if l.get("name")),
            description_raw=j.get("description"), description_text=text_of(j.get("description")),
            compensation_text=comp, posted_date=parse_dt(j.get("publishedOn")),
        ))
    return out


# ── Zoho Recruit ───────────────────────────────────────────────────────────

_ZOHO_LIST_RE = re.compile(r'<input type="hidden" value="([^"]*)" id="jobs"')
_ZOHO_DETAIL_RE = re.compile(r"var jobs = JSON\.parse\('((?:[^'\\]|\\.)*)'\)")


def _js_unescape(s: str) -> str:
    def rep(m: re.Match) -> str:
        e = m.group(1)
        if e[0] in "xu" and len(e) > 1:
            return chr(int(e[1:], 16))
        return {"n": "\n", "t": "\t", "r": "\r"}.get(e, e)
    return re.sub(r"\\(x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|.)", rep, s)


def parse_zoho_list(page: str) -> list[dict]:
    m = _ZOHO_LIST_RE.search(page or "")
    return json.loads(htmllib.unescape(m.group(1))) if m else []


def parse_zoho_detail(page: str) -> dict | None:
    m = _ZOHO_DETAIL_RE.search(page or "")
    if not m:
        return None
    data = json.loads(_js_unescape(m.group(1)))
    return data[0] if data else None


async def zoho_recruit(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    base = f"https://{t.token}/jobs/Careers"
    page = await f.get_text(base)
    if page is None:
        raise HarvestError("zoho careers page not readable")
    jobs = parse_zoho_list(page)
    out = []
    for j in jobs:
        if j.get("Publish") is False:
            continue
        title = j.get("Posting_Title") or j.get("Job_Opening_Name") or ""
        url = f"{base}/{j.get('id')}/{quote(re.sub(r'[^A-Za-z0-9]+', '-', title).strip('-'))}"
        detail = None
        if _needs_detail(title, len(jobs)):
            detail = parse_zoho_detail(await f.get_text(url) or "")
        desc = (detail or {}).get("Job_Description")
        out.append(_result(
            t, title=title, source_id=str(j.get("id")), source_url=url, application_url=url,
            location=", ".join(x for x in (j.get("City"), (detail or {}).get("State"), j.get("Country")) if x),
            work_mode="remote" if j.get("Remote_Job") else "unknown",
            employment_type=j.get("Job_Type"), description_raw=desc, description_text=text_of(desc) or None,
            compensation_text=(detail or {}).get("Salary"),
            posted_date=parse_dt((detail or {}).get("Date_Opened")),
        ))
    return out


# ── Freshteam ──────────────────────────────────────────────────────────────

async def freshteam(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    data = await f.get_json(f"https://{t.token}.freshteam.com/hire/widgets/jobs.json")
    if data is None:
        raise HarvestError("freshteam widget not readable")
    branches = {b.get("id"): b for b in data.get("branches") or []}
    out = []
    for j in data.get("jobs", []):
        if j.get("deleted"):
            continue
        br = branches.get(j.get("branch_id")) or {}
        url = f"https://{t.token}.freshteam.com/jobs/{j.get('unique_id')}"
        out.append(_result(
            t, title=j.get("title", ""), source_id=str(j.get("id")), source_url=url, application_url=url,
            location=br.get("location") or br.get("name"),
            work_mode="remote" if j.get("remote") else "unknown",
            description_raw=j.get("description"), description_text=text_of(j.get("description")),
            posted_date=parse_dt(j.get("created_at")),
        ))
    return out


# ── MyNextHire ─────────────────────────────────────────────────────────────

async def mynexthire(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    base = f"https://{t.token}.mynexthire.com"
    data = await f.post_json(f"{base}/employer/careers/reqlist/get",
                             json={"source": "careers", "code": "", "filterByBuId": -1})
    if data is None:
        raise HarvestError("mynexthire reqlist not readable")
    out = []
    for j in data.get("reqDetailsBOList") or []:
        url = f"{base}/employer/jobs?src=careers#/reqDetails/{j.get('reqId')}"
        lo, hi = j.get("ctcBandLowEnd"), j.get("ctcBandHighEnd")
        comp = f"{j.get('reqCurrency') or ''} {lo} - {hi}" if (lo or hi) else None
        out.append(_result(
            t, title=j.get("reqTitle", ""), source_id=str(j.get("reqId")), source_url=url, application_url=url,
            location=j.get("location") or j.get("locationAddress"),
            employment_type=j.get("employmentType") or ("Fresher" if j.get("fresher") else None),
            description_raw=j.get("jdDisplay"), description_text=text_of(j.get("jdDisplay")),
            compensation_text=comp, posted_date=parse_dt(j.get("approvedOn")),
        ))
    return out


# ── Workday ────────────────────────────────────────────────────────────────

async def workday(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    tenant, wd, site = t.token.split("|")
    host = f"https://{tenant}.{wd}.myworkdayjobs.com"
    api = f"{host}/wday/cxs/{tenant}/{site}"
    postings: list[dict] = []
    total = None
    offset = 0
    while total is None or offset < total:
        data = await f.post_json(f"{api}/jobs", json={"appliedFacets": {}, "limit": 20, "offset": offset, "searchText": ""})
        if data is None:
            if not postings:
                raise HarvestError("workday jobs endpoint not readable")
            break
        page = data.get("jobPostings") or []
        if total is None:
            total = data.get("total") or 0
        postings.extend(page)
        offset += 20
        if not page or offset >= 2000:  # Workday stops paging around 2,000
            break
    out = []
    for j in postings:
        path = j.get("externalPath") or ""
        url = f"{host}/{site}{path}"
        info: dict = {}
        if _needs_detail(j.get("title", ""), len(postings)):
            d = await f.get_json(f"{api}{path}")
            info = (d or {}).get("jobPostingInfo") or {}
        out.append(_result(
            t, title=j.get("title", ""), source_id=path.rsplit("_", 1)[-1] or path, source_url=info.get("externalUrl") or url,
            application_url=info.get("externalUrl") or url, location=info.get("location") or j.get("locationsText"),
            employment_type=info.get("timeType"), description_raw=info.get("jobDescription"),
            description_text=text_of(info.get("jobDescription")) or None,
            posted_date=parse_dt(info.get("startDate")),
        ))
    return out


# ── Personio ───────────────────────────────────────────────────────────────
# Public XML feed https://{sub}.jobs.personio.de/xml (also .com); job page /job/{id}.
# <jobDescriptions><jobDescription><name/><value/></jobDescription>...</jobDescriptions> carries
# the text when the company fills it in (Personio's own board leaves it empty).

def _xml_text(block: str, tag: str) -> str | None:
    m = re.search(rf"<{tag}>(.*?)</{tag}>", block, re.S)
    return htmllib.unescape(m.group(1).strip()) if m else None


def parse_personio(xml: str, t: Target, host: str) -> list[SourceResult]:
    out = []
    for block in re.findall(r"<position>(.*?)</position>", xml, re.S):
        pid = _xml_text(block, "id")
        offices = [_xml_text(block, "office")] + re.findall(r"<office>(.*?)</office>", _xml_text(block, "additionalOffices") or "")
        sections = re.findall(r"<jobDescription>\s*<name>(.*?)</name>\s*<value>(.*?)</value>", block, re.S)
        raw = "".join(f"<h3>{htmllib.unescape(n)}</h3>{htmllib.unescape(v)}" for n, v in sections)
        raw = re.sub(r"<!\[CDATA\[(.*?)\]\]>", r"\1", raw, flags=re.S)
        url = f"https://{host}/job/{pid}"
        out.append(_result(
            t, title=_xml_text(block, "name") or "", source_id=pid, source_url=url, application_url=url,
            location=", ".join(dict.fromkeys(o for o in offices if o)) or None,
            employment_type=" ".join(x for x in (_xml_text(block, "employmentType"), _xml_text(block, "schedule")) if x) or None,
            description_raw=raw or None, description_text=text_of(raw) or None,
            posted_date=parse_dt(_xml_text(block, "createdAt")),
        ))
    return out


async def personio(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    for host in (f"{t.token}.jobs.personio.de", f"{t.token}.jobs.personio.com"):
        resp = await f.get(f"https://{host}/xml")
        if resp is not None and resp.status_code == 200 and "<workzag-jobs" in resp.text:
            return parse_personio(resp.text, t, host)
    raise HarvestError("personio feed not readable")


# ── Teamtailor ─────────────────────────────────────────────────────────────
# RSS https://{sub}.teamtailor.com/jobs.rss (token may also be a custom careers host), with
# remoteStatus and <tt:locations>.

def parse_teamtailor(rss: str, t: Target) -> list[SourceResult]:
    out = []
    for item in re.findall(r"<item>(.*?)</item>", rss, re.S):
        desc = _xml_text(item, "description") or ""
        cities = re.findall(r"<tt:city>(.*?)</tt:city>", item)
        countries = re.findall(r"<tt:country>(.*?)</tt:country>", item)
        remote = (_xml_text(item, "remoteStatus") or "").lower()
        link = _xml_text(item, "link")
        out.append(_result(
            t, title=_xml_text(item, "title") or "", source_id=_xml_text(item, "guid") or link,
            source_url=link, application_url=link,
            location=", ".join(dict.fromkeys(f"{c}, {k}" for c, k in zip(cities, countries))) or None,
            work_mode={"fully": "remote", "remote": "remote", "hybrid": "hybrid", "none": "onsite"}.get(remote, "unknown"),
            description_raw=desc or None, description_text=text_of(desc) or None,
            posted_date=_rss_date(_xml_text(item, "pubDate")),
        ))
    return out


def _rss_date(value: str | None) -> datetime | None:
    from email.utils import parsedate_to_datetime
    try:
        return parsedate_to_datetime(value) if value else None
    except (TypeError, ValueError):
        return None


async def teamtailor(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    host = t.token if "." in t.token else f"{t.token}.teamtailor.com"
    resp = await f.get(f"https://{host}/jobs.rss")
    if resp is None or resp.status_code != 200 or "<rss" not in resp.text[:500]:
        raise HarvestError("teamtailor feed not readable")
    return parse_teamtailor(resp.text, t)


# ── Breezy HR ──────────────────────────────────────────────────────────────
# JSON list https://{sub}.breezy.hr/json (no descriptions; the job page is {url}).

async def breezy(f: AsyncFetcher, t: Target) -> list[SourceResult]:
    data = await f.get_json(f"https://{t.token}.breezy.hr/json")
    if not isinstance(data, list):
        raise HarvestError("breezy board not readable")
    out = []
    for j in data:
        loc = j.get("location") or {}
        out.append(_result(
            t, title=j.get("name", ""), source_id=j.get("id"), source_url=j.get("url"), application_url=j.get("url"),
            location=loc.get("name"), work_mode="remote" if loc.get("is_remote") else "unknown",
            employment_type=(j.get("type") or {}).get("name"), compensation_text=j.get("salary") or None,
            posted_date=parse_dt(j.get("published_date")),
        ))
    return out


HarvestFn = Callable[[AsyncFetcher, Target], Awaitable[list[SourceResult]]]

# Darwinbox (browser-based) and custom pages are registered in autoapply/ats/browser.py and
# autoapply/ats/custom.py; the runner merges all three.
HTTP_HARVESTERS: dict[str, HarvestFn] = {
    "greenhouse": greenhouse, "lever": lever, "ashby": ashby, "workable": workable,
    "smartrecruiters": smartrecruiters, "recruitee": recruitee, "keka": keka,
    "zoho_recruit": zoho_recruit, "freshteam": freshteam, "mynexthire": mynexthire, "workday": workday,
    "personio": personio, "teamtailor": teamtailor, "breezy": breezy,
}
