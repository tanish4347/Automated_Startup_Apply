"""Harvest the questions real application forms ask. Nothing is ever submitted.

Classic ATSs (plain HTTP; endpoints verified 2026-09-30):
  greenhouse   GET boards-api.greenhouse.io/v1/boards/{token}/jobs/{id}?questions=true
               -> questions[], location_questions[], demographic_questions, compliance[]
  ashby        POST jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting (the public query the
               job page itself runs) -> applicationForm.sections[].fieldEntries[]
  lever        no JSON endpoint exposes the form. GET jobs.lever.co/{co}/{id}/apply returns the
               form itself, server-rendered: li.application-question with real `required`
               attributes and names, custom questions as cards[...] inputs. Parsed as HTML.
  smartrecruiters  the public posting API has no form. The apply app's
               /oneclick-ui/api/company/{cid}/publication/{uuid}/config (field sets, required
               flags) is behind DataDome for plain HTTP: harvested through the browser tier.
Sampling is by distinct company (cap per platform, default 150), up to 2 postings each,
internship-like titles first, so one company with 400 postings counts once.

Job platforms (browser tier, logged-in profile, READ-ONLY): Internshala, Unstop, Naukri, Instahyre.
The page is opened, the apply control clicked, and every visible form field dumped. Every
non-GET request is aborted before it leaves the browser, so no click can submit anything; a
platform whose "Apply" is itself the submission simply shows no form, and that is reported.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable

from bs4 import BeautifulSoup
from sqlalchemy.orm import Session

from autoapply.ats.harvesters import DETAIL_HINT_RE
from autoapply.ats.http import AsyncFetcher
from autoapply.logging import get_logger

log = get_logger(__name__)


@dataclass
class HarvestedField:
    label: str
    field_type: str
    required: bool = False
    options: list[str] | None = None
    section: str = "questions"


@dataclass
class HarvestedForm:
    platform: str
    company_key: str                 # board token, or the platform itself for platform forms
    company_name: str | None
    url: str
    fields: list[HarvestedField] = field(default_factory=list)
    job_id: int | None = None


def clean_label(label: str | None) -> str:
    label = re.sub(r"<[^>]+>", " ", label or "")
    label = re.sub(r"\s+", " ", label).strip()
    return re.sub(r"\s*[✱*]+\s*$", "", label).strip()


# ── Greenhouse ───────────────────────────────────────────────────────────────

_GH_TYPES = {"input_text": "text", "textarea": "textarea", "input_file": "file", "input_hidden": "hidden",
             "multi_value_single_select": "select", "multi_value_multi_select": "multi_select"}


def parse_greenhouse(data: dict[str, Any]) -> list[HarvestedField]:
    out: list[HarvestedField] = []
    for q in data.get("questions") or []:
        fields = [f for f in q.get("fields") or [] if f.get("type") != "input_hidden"]
        if not fields:
            continue
        types = {f.get("type") for f in fields}
        ftype = "file" if "input_file" in types else _GH_TYPES.get(fields[0].get("type"), fields[0].get("type") or "text")
        options = [v.get("label") for f in fields for v in f.get("values") or [] if v.get("label")]
        out.append(HarvestedField(clean_label(q.get("label")), ftype, bool(q.get("required")), options or None))
    for q in data.get("location_questions") or []:
        label = clean_label(q.get("label") or "Location")
        if label.lower() in ("latitude", "longitude"):  # filled by the location autocomplete, not asked
            continue
        out.append(HarvestedField(label, "location", bool(q.get("required")), section="location"))
    demo = data.get("demographic_questions") or {}
    for q in demo.get("questions") or [] if isinstance(demo, dict) else []:
        opts = [a.get("label") for a in q.get("answer_options") or [] if a.get("label")]
        out.append(HarvestedField(clean_label(q.get("label")), "multi_select" if "multi" in (q.get("type") or "") else "select",
                                  bool(q.get("required")), opts or None, section="demographic"))
    for block in data.get("compliance") or []:
        for q in block.get("questions") or []:
            fields = q.get("fields") or []
            opts = [v.get("label") for f in fields for v in f.get("values") or [] if v.get("label")]
            label = q.get("label") or re.sub(r"<[^>]+>", " ", q.get("description") or "")[:120]
            out.append(HarvestedField(clean_label(label), "select", bool(q.get("required")), opts or None, section="demographic"))
    return [f for f in out if f.label]


async def harvest_greenhouse(f: AsyncFetcher, token: str, job_ids: list[str]) -> list[tuple[str, list[HarvestedField]]]:
    out = []
    for jid in job_ids:
        data = await f.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}", params={"questions": "true"})
        if isinstance(data, dict) and data.get("questions") is not None:
            out.append((data.get("absolute_url") or f"greenhouse:{token}/{jid}", parse_greenhouse(data)))
    return out


async def greenhouse_jobs(f: AsyncFetcher, token: str) -> list[tuple[str, str]]:
    data = await f.get_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs")
    return [(str(j["id"]), j.get("title") or "") for j in (data or {}).get("jobs") or []]


# ── Ashby ────────────────────────────────────────────────────────────────────

ASHBY_QUERY = """query ApiJobPosting($organizationHostedJobsPageName: String!, $jobPostingId: String!) {
  jobPosting(organizationHostedJobsPageName: $organizationHostedJobsPageName, jobPostingId: $jobPostingId) {
    id title applicationForm { sections { title fieldEntries { ... on FormFieldEntry { id field isRequired } } } }
  }
}"""
_ASHBY_TYPES = {"String": "text", "Email": "email", "Phone": "phone", "File": "file", "LongText": "textarea",
                "ValueSelect": "select", "MultiValueSelect": "multi_select", "Boolean": "boolean", "Date": "date",
                "Number": "number", "Location": "location", "Score": "select", "SocialLink": "url"}


def parse_ashby(data: dict[str, Any]) -> list[HarvestedField]:
    posting = ((data or {}).get("data") or {}).get("jobPosting") or {}
    out = []
    for section in (posting.get("applicationForm") or {}).get("sections") or []:
        for entry in section.get("fieldEntries") or []:
            fld = entry.get("field") or {}
            opts = [v.get("label") for v in fld.get("selectableValues") or [] if v.get("label")]
            ftype = _ASHBY_TYPES.get(fld.get("type"), (fld.get("type") or "text").lower())
            out.append(HarvestedField(clean_label(fld.get("title")), ftype, bool(entry.get("isRequired")), opts or None,
                                      section="system" if (fld.get("path") or "").startswith("_systemfield") else "questions"))
    return [f for f in out if f.label]


async def harvest_ashby(f: AsyncFetcher, org: str, job_ids: list[str]) -> list[tuple[str, list[HarvestedField]]]:
    out = []
    for jid in job_ids:
        resp = await f.post("https://jobs.ashbyhq.com/api/non-user-graphql", params={"op": "ApiJobPosting"},
                            json={"operationName": "ApiJobPosting", "query": ASHBY_QUERY,
                                  "variables": {"organizationHostedJobsPageName": org, "jobPostingId": jid}})
        if resp is not None and resp.status_code == 200:
            fields = parse_ashby(resp.json())
            if fields:
                out.append((f"https://jobs.ashbyhq.com/{org}/{jid}/application", fields))
    return out


async def ashby_jobs(f: AsyncFetcher, org: str) -> list[tuple[str, str]]:
    data = await f.get_json(f"https://api.ashbyhq.com/posting-api/job-board/{org}")
    return [(j["id"], j.get("title") or "") for j in (data or {}).get("jobs") or [] if j.get("id")]


# ── Lever ────────────────────────────────────────────────────────────────────

def parse_lever_form(html: str) -> list[HarvestedField]:
    soup = BeautifulSoup(html, "lxml")
    out: list[HarvestedField] = []
    for li in soup.select("li.application-question"):
        label_el = li.select_one(".application-label") or li.select_one(".text")
        label = clean_label(label_el.get_text(" ", strip=True) if label_el else "")
        controls = li.find_all(["input", "textarea", "select"])
        controls = [c for c in controls if c.get("type") not in ("hidden", "submit", "button")]
        if not label or not controls:
            continue
        c = controls[0]
        required = "✱" in (label_el.get_text() if label_el else "") or any(x.has_attr("required") for x in controls)
        if c.name == "select":
            ftype, opts = "select", [o.get_text(strip=True) for o in c.find_all("option") if o.get("value")]
        elif c.get("type") in ("radio", "checkbox"):
            ftype = "select" if c.get("type") == "radio" else "multi_select"
            opts = [clean_label(x.parent.get_text(" ", strip=True)) for x in controls if x.get("type") == c.get("type")]
        else:
            ftype = {"file": "file", "email": "email", "tel": "phone"}.get(c.get("type"), "textarea" if c.name == "textarea" else "text")
            opts = None
        name = c.get("name") or ""
        section = "demographic" if name.startswith("eeo[") else "questions"
        out.append(HarvestedField(label, ftype, required, opts or None, section=section))
    return out


async def harvest_lever(f: AsyncFetcher, co: str, job_ids: list[str]) -> list[tuple[str, list[HarvestedField]]]:
    out = []
    for jid in job_ids:
        url = f"https://jobs.lever.co/{co}/{jid}/apply"
        html = await f.get_text(url)
        if html:
            fields = parse_lever_form(html)
            if fields:
                out.append((url, fields))
    return out


async def lever_jobs(f: AsyncFetcher, co: str) -> list[tuple[str, str]]:
    data = await f.get_json(f"https://api.lever.co/v0/postings/{co}", params={"mode": "json"})
    return [(j["id"], j.get("text") or "") for j in data or [] if isinstance(j, dict) and j.get("id")]


ATS_HARVEST = {
    "greenhouse": (greenhouse_jobs, harvest_greenhouse),
    "ashby": (ashby_jobs, harvest_ashby),
    "lever": (lever_jobs, harvest_lever),
}


def pick_jobs(jobs: list[tuple[str, str]], n: int = 2) -> list[str]:
    """Internship-like postings first (their forms are the ones we will fill), then the rest."""
    ranked = sorted(jobs, key=lambda j: not DETAIL_HINT_RE.search(j[1]))
    return [jid for jid, _ in ranked[:n]]


async def harvest_ats(platform: str, tokens: list[tuple[str, str | None]], *, jobs_per_company: int = 2,
                      concurrency: int = 8, fetcher: AsyncFetcher | None = None) -> list[HarvestedForm]:
    """tokens: [(board token, company name)] - one entry per distinct company."""
    list_jobs, harvest = ATS_HARVEST[platform]
    sem = asyncio.Semaphore(concurrency)
    forms: list[HarvestedForm] = []

    async def one(f: AsyncFetcher, token: str, name: str | None) -> None:
        async with sem:
            try:
                jobs = await list_jobs(f, token)
                for url, fields in await harvest(f, token, pick_jobs(jobs, jobs_per_company)):
                    forms.append(HarvestedForm(platform, token.lower(), name, url, fields))
            except Exception as e:
                log.warning("question_harvest_failed", platform=platform, token=token, error=str(e)[:120])

    if fetcher is not None:
        await asyncio.gather(*(one(fetcher, t, n) for t, n in tokens))
        return forms
    async with AsyncFetcher(per_host=3, max_retries=1) as f:
        await asyncio.gather(*(one(f, t, n) for t, n in tokens))
    return forms


# ── company sampling ─────────────────────────────────────────────────────────

_URL_TOKENS = {
    "greenhouse": re.compile(r"greenhouse\.io/(?:embed/job_app\?for=)?([A-Za-z0-9_-]+)"),
    "lever": re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)/"),
    "ashby": re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.%-]+)/"),
    "smartrecruiters": re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)/\d"),
}


def sample_companies(session: Session, platform: str, cap: int = 150) -> list[tuple[str, str | None]]:
    """Distinct board tokens for a platform: resolved companies first (priority, India), then
    tokens seen in stored job URLs. One entry per company."""
    from autoapply.models.company import Company
    from autoapply.models.job import Job
    seen: dict[str, str | None] = {}
    rows = (session.query(Company.ats_token, Company.name).filter(Company.ats_type == platform, Company.ats_token.isnot(None))
            .order_by(Company.priority.desc(), Company.is_india.desc()).all())
    for token, name in rows:
        seen.setdefault(token.lower(), name)
    pattern = _URL_TOKENS.get(platform)
    if pattern is not None:
        for company, url in session.query(Job.company, Job.application_url).filter(Job.application_url.like(f"%{platform}%")):
            m = pattern.search(url or "")
            if m and m.group(1).lower() not in ("embed", "v1", "boards"):
                seen.setdefault(m.group(1).lower(), company)
    return list(seen.items())[:cap]


# ── persistence ──────────────────────────────────────────────────────────────

def store_forms(session: Session, forms: Iterable[HarvestedForm]) -> int:
    """Upsert one row per (platform, company, label, type); times_seen counts postings."""
    from autoapply.models.form_question import FormQuestion
    from autoapply.services.company_service import get_company
    now = datetime.now(timezone.utc)
    written = 0
    for form in forms:
        company = get_company(session, form.company_name) if form.company_name else None
        per_form: set[tuple[str, str]] = set()
        for fld in form.fields:
            key = (fld.label[:1000], fld.field_type)
            if key in per_form:
                continue
            per_form.add(key)
            row = (session.query(FormQuestion)
                   .filter_by(platform=form.platform, company_key=form.company_key, raw_label=key[0], field_type=key[1]).first())
            if row is None:
                row = FormQuestion(platform=form.platform, company_key=form.company_key, raw_label=key[0],
                                   field_type=key[1], times_seen=0, first_seen=now)
                session.add(row)
            row.times_seen = (row.times_seen or 0) + 1
            row.is_required = bool(row.is_required) or fld.required
            row.options_json = fld.options or row.options_json
            row.section = fld.section
            row.company_id = company.id if company else row.company_id
            row.job_id = form.job_id or row.job_id
            row.example_url = form.url
            row.last_seen = now
            written += 1
    session.flush()
    _refresh_distinct_companies(session)
    session.commit()
    return written


def normalize_label(label: str) -> str:
    t = label.lower()
    t = re.sub(r"\(optional\)|\(required\)|please|kindly", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def _refresh_distinct_companies(session: Session) -> None:
    from autoapply.models.form_question import FormQuestion
    counts: dict[tuple[str, str], set[str]] = {}
    rows = session.query(FormQuestion).all()
    for r in rows:
        counts.setdefault((r.platform, normalize_label(r.raw_label)), set()).add(r.company_key)
    for r in rows:
        r.distinct_companies = len(counts[(r.platform, normalize_label(r.raw_label))])
