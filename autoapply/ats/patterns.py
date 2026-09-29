"""Recognize ATS job-board URLs and extract (ats_type, token).

Tokens are what the harvesters need to call each ATS:
  greenhouse/lever/ashby/workable/smartrecruiters: the board slug
  recruitee/keka/darwinbox/freshteam/mynexthire:    the subdomain
  zoho_recruit:                                     '<sub>.zohorecruit.<com|in>'
  workday:                                          '<tenant>|<wdN>|<site>'
  google_form:                                      the form URL
  turbohire:                                        the subdomain (detected only; no harvester yet)
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SLUG = r"([A-Za-z0-9][A-Za-z0-9_.-]*)"
_SUB = r"([a-z0-9][a-z0-9-]*)"

# (ats_type, pattern). The first capture group is the token unless noted in _token().
_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("greenhouse", re.compile(r"greenhouse\.io/embed/job_(?:board|app)(?:/js)?\?[^\"'\s]*?\bfor=" + _SLUG, re.I)),
    ("greenhouse", re.compile(r"boards-api\.greenhouse\.io/v1/boards/" + _SLUG, re.I)),
    ("greenhouse", re.compile(r"(?:job-)?boards(?:\.eu)?\.greenhouse\.io/(?!embed\b)" + _SLUG, re.I)),
    ("lever", re.compile(r"(?:jobs|api)(?:\.eu)?\.lever\.co/(?:v0/postings/)?" + _SLUG, re.I)),
    ("ashby", re.compile(r"jobs\.ashbyhq\.com/" + _SLUG, re.I)),
    ("ashby", re.compile(r"api\.ashbyhq\.com/posting-api/job-board/" + _SLUG, re.I)),
    ("workable", re.compile(r"apply\.workable\.com/(?:api/v\d/accounts/)?(?!api\b)" + _SLUG, re.I)),
    ("workable", re.compile(r"\b" + _SUB + r"\.workable\.com\b", re.I)),
    ("smartrecruiters", re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/" + _SLUG, re.I)),
    ("smartrecruiters", re.compile(r"api\.smartrecruiters\.com/v1/companies/" + _SLUG, re.I)),
    ("recruitee", re.compile(r"\b" + _SUB + r"\.recruitee\.com\b", re.I)),
    ("keka", re.compile(r"\b" + _SUB + r"\.keka\.com\b", re.I)),
    ("zoho_recruit", re.compile(r"\b(" + _SUB[1:-1] + r"\.zohorecruit\.(?:com|in|eu))\b", re.I)),
    ("darwinbox", re.compile(r"\b" + _SUB + r"\.darwinbox\.(?:in|com)\b", re.I)),
    ("freshteam", re.compile(r"\b" + _SUB + r"\.freshteam\.com\b", re.I)),
    ("mynexthire", re.compile(r"\b" + _SUB + r"\.mynexthire\.com\b", re.I)),
    ("workday", re.compile(r"\b" + _SUB + r"\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?" + _SLUG, re.I)),
    ("turbohire", re.compile(r"\b" + _SUB + r"\.turbohire\.co\b", re.I)),
    ("keka", re.compile(r"\b" + _SUB + r"\.kekahire\.com\b", re.I)),
    ("bamboohr", re.compile(r"\b" + _SUB + r"\.bamboohr\.com\b", re.I)),
    ("jazzhr", re.compile(r"\b" + _SUB + r"\.applytojob\.com\b", re.I)),
    ("personio", re.compile(r"\b" + _SUB + r"\.jobs\.personio\.(?:de|com)\b", re.I)),
    ("teamtailor", re.compile(r"\b" + _SUB + r"\.teamtailor\.com\b", re.I)),
    ("breezy", re.compile(r"\b" + _SUB + r"\.breezy\.hr\b", re.I)),
    ("pinpoint", re.compile(r"\b" + _SUB + r"\.pinpointhq\.com\b", re.I)),
    ("recruiterflow", re.compile(r"recruiterflow\.com/" + _SLUG + r"/jobs", re.I)),
    ("skillate", re.compile(r"\b" + _SUB + r"\.skillate\.com\b", re.I)),
    ("springrecruit", re.compile(r"\b" + _SUB + r"\.springrecruit\.com\b", re.I)),
    ("hirepro", re.compile(r"\b" + _SUB + r"\.hirepro\.in\b", re.I)),
    ("google_form", re.compile(r"(https?://(?:docs\.google\.com/forms/d/(?:e/)?[A-Za-z0-9_-]+|forms\.gle/[A-Za-z0-9]+))", re.I)),
]

# Path segments / subdomains that are part of an ATS's own site, not a company token.
_NOT_TOKENS = {
    "greenhouse": {"embed", "v1", "boards", "api", "static", "images", "assets"},
    "lever": {"v0", "api", "static", "assets", "legal"},
    "ashby": {"api", "static", "assets"},
    "workable": {"api", "www", "apply", "resources", "static", "careers", "help", "jobs"},
    "smartrecruiters": {"api", "static", "assets", "legal", "www"},
    "recruitee": {"www", "api", "app", "careers", "support", "blog", "docs"},
    "keka": {"www", "app", "cdn", "help", "blog", "docs", "api", "login"},
    "darwinbox": {"www", "cdn", "api", "blog", "help", "docs", "static"},
    "freshteam": {"www", "api", "developers", "support", "assets", "help"},
    "mynexthire": {"www", "api", "app", "help"},
    "turbohire": {"www", "api", "app", "help"},
    "bamboohr": {"www", "api", "app", "help", "partners", "marketplace", "status", "documentation"},
    "jazzhr": {"www", "app", "help"},
    "personio": {"www", "api", "support"},
    "teamtailor": {"www", "app", "api", "support", "assets", "docs", "status", "partner"},
    "breezy": {"www", "app", "api", "help", "developer"},
    "pinpoint": {"www", "app", "api", "help", "developers"},
    "recruiterflow": {"www", "app", "api", "blog"},
    "skillate": {"www", "app", "api"},
    "springrecruit": {"www", "app", "api"},
    "hirepro": {"www", "app", "api"},
    "workday": set(),
    "zoho_recruit": {"www.zohorecruit.com", "www.zohorecruit.in"},
    "google_form": set(),
}

# Types that have a harvester (turbohire/google_form are recorded but not harvested).
HARVESTABLE = {
    "greenhouse", "lever", "ashby", "workable", "smartrecruiters", "recruitee", "keka",
    "zoho_recruit", "darwinbox", "freshteam", "mynexthire", "workday",
    "personio", "teamtailor", "breezy",
}
# Detected and recorded, but no verified public jobs endpoint yet: their jobs go to the generic
# careers-page crawler. BambooHR's /careers/list and JazzHR's /apply are reachable but no board
# with open jobs was found to verify a parser against (2026-09-30).
DETECT_ONLY = {"turbohire", "bamboohr", "jazzhr", "pinpoint", "recruiterflow", "skillate", "springrecruit",
               "hirepro", "google_form"}


@dataclass(frozen=True)
class AtsRef:
    ats_type: str
    token: str
    evidence: str  # the matched URL fragment

    @property
    def key(self) -> tuple[str, str]:
        return self.ats_type, self.token.lower()


def _token(ats_type: str, m: re.Match) -> str | None:
    if ats_type == "workday":
        tenant, wd, site = m.group(1), m.group(2), m.group(3)
        if site.lower() in {"wday", "en-us"}:
            return None
        return f"{tenant.lower()}|{wd.lower()}|{site}"
    token = m.group(1).rstrip(".-_")
    if ats_type == "zoho_recruit":
        token = token.lower()
    if token.lower() in _NOT_TOKENS.get(ats_type, set()):
        return None
    return token


def find_ats_refs(text: str) -> list[AtsRef]:
    """All ATS references in a URL or HTML/JSON text, de-duplicated, in order of appearance."""
    found: dict[tuple[str, str], tuple[int, AtsRef]] = {}
    for ats_type, pattern in _PATTERNS:
        for m in pattern.finditer(text or ""):
            token = _token(ats_type, m)
            if not token:
                continue
            ref = AtsRef(ats_type, token, m.group(0)[:160])
            if ref.key not in found:
                found[ref.key] = (m.start(), ref)
    return [ref for _, ref in sorted(found.values(), key=lambda x: x[0])]


def parse_ats_url(url: str) -> AtsRef | None:
    refs = find_ats_refs(url)
    return refs[0] if refs else None


def board_url(ats_type: str, token: str) -> str | None:
    """Human-facing careers page for a detected board."""
    if ats_type == "workday":
        tenant, wd, site = token.split("|")
        return f"https://{tenant}.{wd}.myworkdayjobs.com/{site}"
    return {
        "greenhouse": f"https://job-boards.greenhouse.io/{token}",
        "lever": f"https://jobs.lever.co/{token}",
        "ashby": f"https://jobs.ashbyhq.com/{token}",
        "workable": f"https://apply.workable.com/{token}/",
        "smartrecruiters": f"https://jobs.smartrecruiters.com/{token}",
        "recruitee": f"https://{token}.recruitee.com/",
        "keka": f"https://{token}.keka.com/careers/",
        "zoho_recruit": f"https://{token}/jobs/Careers",
        "darwinbox": f"https://{token}.darwinbox.in/ms/candidatev2/main/careers/allJobs",
        "freshteam": f"https://{token}.freshteam.com/jobs",
        "mynexthire": f"https://{token}.mynexthire.com/employer/jobs",
        "turbohire": f"https://{token}.turbohire.co/",
        "bamboohr": f"https://{token}.bamboohr.com/careers",
        "jazzhr": f"https://{token}.applytojob.com/apply",
        "personio": f"https://{token}.jobs.personio.com/",
        "teamtailor": f"https://{token}.teamtailor.com/jobs",
        "breezy": f"https://{token}.breezy.hr/",
        "pinpoint": f"https://{token}.pinpointhq.com/",
        "recruiterflow": f"https://recruiterflow.com/{token}/jobs",
        "skillate": f"https://{token}.skillate.com/",
        "springrecruit": f"https://{token}.springrecruit.com/",
        "hirepro": f"https://{token}.hirepro.in/",
        "google_form": token,
    }.get(ats_type)
