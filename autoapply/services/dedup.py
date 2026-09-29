"""Job deduplication (v2).

A job's identity is: normalized company + normalized title + place. source_id is deliberately
not part of the key, so the same posting from LinkedIn, Internshala and the company's ATS collapses.

When in doubt this under-merges: a duplicate row is cheap, a wrongly merged job is a lost
application. So "intern" stays in the key (an internship is not the full-time role of the same
name), season and year stay (Summer 2027 and Winter 2027 are separate cohorts), and an unknown
city is keyed by its raw text, never collapsed to its country.
"""

from __future__ import annotations

import hashlib
import re

from sqlalchemy.orm import Session

from autoapply.logging import get_logger
from autoapply.models.job import Job
from autoapply.sources.location import normalize_location, strip_cities

log = get_logger(__name__)

_LEGAL_SUFFIX_RE = re.compile(
    r"\b(private limited|pvt\.? ltd\.?|pvt|ltd|limited|inc|llc|llp|corp|corporation|gmbh|co)\b\.?", re.I)

# Words about the posting's location or pay, not the role: "SDE Intern (Remote, Paid)" == "SDE Intern".
_TITLE_NOISE_RE = re.compile(
    r"\b(remote|hybrid|on[\s-]?site|work from home|wfh|paid|unpaid)\b", re.I)
_INTERN_RE = re.compile(r"\b(interns|internships?)\b", re.I)


def normalize_string(s: str | None) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


# Generic words that trail a legal entity name: "Razorpay Software Pvt Ltd" is the brand "Razorpay".
_ENTITY_TAIL_RE = re.compile(
    r"(\s+(software|technologies|technology|tech|solutions|services|systems|labs|india|ventures"
    r"|innovations|global|digital|internet|consulting))+\s*$", re.I)


def normalize_company(name: str | None) -> str:
    """Brand-level company key.

    Generic tail words are dropped only when a legal suffix is present, because Indian entity
    names routinely add them ("Razorpay Software Pvt Ltd" == "Razorpay"). Tradeoff: a brand that
    really ends in one of those words loses it too ("Zeta Labs Pvt Ltd" -> "zeta", while a bare
    "Zeta Labs" -> "zetalabs")."""
    raw = name or ""
    stripped = _LEGAL_SUFFIX_RE.sub(" ", raw).strip(" .,")
    if stripped != raw.strip(" .,"):
        stripped = _ENTITY_TAIL_RE.sub("", stripped) or stripped
    return normalize_string(stripped)


def normalize_title(title: str | None) -> str:
    """Order-insensitive role key: "Software Engineer, Intern" == "Intern - Software Engineer",
    "SDE Internship - Bangalore" == "SDE Intern" (the city is part of the place key instead)."""
    t = _INTERN_RE.sub("intern", _TITLE_NOISE_RE.sub(" ", title or ""))
    words = re.findall(r"[a-z0-9]+", strip_cities(t).lower())
    return "-".join(sorted(words))


def place_key(location: str | None, title: str | None = None, work_mode: str | None = None) -> str:
    """'remote[-country]', else the canonical city, else the raw location text, else 'unknown'.

    Remote keeps its country: "Remote - India" and "Remote - US" differ in who may apply."""
    loc = normalize_location(location, title)
    if (work_mode or loc.work_mode) == "remote":
        return "remote-" + normalize_string(loc.country) if loc.country else "remote"
    return normalize_string(loc.city) or normalize_string(location)[:60] or "unknown"


def compute_dedup_hash(
    title: str,
    company: str | None,
    location: str | None,
    work_mode: str | None = None,
) -> str:
    key = f"{normalize_company(company)}::{normalize_title(title)}::{place_key(location, title, work_mode)}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def get_duplicate(session: Session, dedup_hash: str) -> Job | None:
    return session.query(Job).filter(Job.dedup_hash == dedup_hash).first()


def deduplicate_job(
    session: Session,
    title: str,
    company: str | None,
    location: str | None,
    work_mode: str | None = None,
) -> tuple[str, Job | None]:
    h = compute_dedup_hash(title, company, location, work_mode)
    return h, get_duplicate(session, h)
