"""Seed the question catalog (vault_answer rows) from docs/QUESTIONS.md.

docs/QUESTIONS.md is the only source of what to ask: nothing here invents a question. Seeded:
- every cluster above the report's 2% stopping line, and
- every rule-based (non-custom) cluster below it: those are generic questions (notice period,
  GPA, timezone ...) that recur across companies, just less often.
Company-specific custom.* clusters below the line are not seeded; they are answered per form.

Each row: canonical_key, category, question (the most common real phrasing), empty answer,
status NEEDS REVIEW, sensitivity SENSITIVE for attestations (autoapply/candidate/sensitive.py),
and the intake pass that asks it: cv (facts the CV can fill), gap, policy, story (long free text).
Idempotent: an existing row keeps its answer, source and status.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.orm import Session

from autoapply.candidate.sensitive import SENSITIVE, is_sensitive_key
from autoapply.config import PROJECT_ROOT

QUESTIONS_MD = PROJECT_ROOT / "docs" / "QUESTIONS.md"

CATEGORIES = {
    "identity": {"first_name", "last_name", "full_name", "preferred_name", "email", "phone", "location_current"},
    "links": {"linkedin_url", "github_url", "twitter_url", "portfolio_url"},
    "documents": {"resume"},
    "education": {"university", "degree", "major", "gpa", "graduation_date"},
    "experience": {"current_company", "current_title", "years_experience", "previously_employed"},
    "eligibility": {"work_authorization", "visa_sponsorship", "age_18_plus"},
    "logistics": {"relocation", "start_date", "notice_period", "work_mode_ok", "timezone", "languages"},
    "compensation": {"salary_expectation"},
    "demographic": {"gender", "race_ethnicity", "veteran_status", "disability_status", "pronouns"},
    "sourcing": {"how_heard", "referral"},
    "consent": {"privacy_consent", "ai_policy_ack"},
    "story": {"why_company", "additional_info", "cover_letter"},
}
# Keys the policy block (intake pass c) answers with structured rules.
POLICY_KEYS = {"relocation", "start_date", "notice_period", "salary_expectation", "work_mode_ok"}
# Keys the CV can fill (intake pass a), directly or as a suggestion.
CV_KEYS = {"first_name", "last_name", "full_name", "email", "phone", "location_current", "linkedin_url",
           "github_url", "portfolio_url", "resume", "university", "degree", "major", "current_company",
           "current_title", "gpa", "graduation_date", "custom.skills", "custom.organization_name"}

_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*`([^`]+)`[^|]*\|\s*([\d.]+)%\s*\|\s*(\d+)%\s*\|\s*(\d+)\s*\|([^|]*)\|([^|]*)\|(.*)\|\s*$")


@dataclass
class CatalogEntry:
    key: str
    weight: float           # weighted volume, 0-1
    required_rate: float
    companies: int
    field_type: str
    question: str
    above_line: bool


def category_of(key: str, field_type: str) -> str:
    base = key.split(".")[0]
    for cat, keys in CATEGORIES.items():
        if base in keys:
            return cat
    if key.startswith("custom.") and is_sensitive_key(key):
        return "eligibility"
    return "story" if field_type == "textarea" else "custom"


def classify_card(key: str, field_type: str, category: str, question: str) -> str:
    from autoapply.candidate.sensitive import is_sensitive_key
    q = (question or "").strip().lower()
    
    # 1. Invalid/Noise
    if q in {"attach", "submit", "upload", "continue", "next", "save", "search", "type"}:
        return "INVALID_NOISE"
        
    # 2. Documents
    if field_type == "file":
        return "DOCUMENT"
        
    base = key.split(".")[0]
    
    # 3. Contextual Application Decisions
    CONTEXTUAL_KEYS = {
        "internship_duration_preferred",
        "internship_start_date",
        "internship_end_date",
        "internship_availability_full",
        "internship_full_time",
        "internship_academic_commitments",
        "internship_noc_available",
        "internship_academic_credit",
        "internship_can_extend",
        "unpaid_ok",
        "other_compensation_expectations",
        "relocation_costs_bearer",
        "location_current_proximity",
        "previous_employment",
        "previous_application",
        "department",
        "role_preference",
        "relatives_working_here",
        "non_compete_signed",
        "nda_ok",
        "workplace_accommodations", 
        "consent_data_storage",
        "additional_relevant_info",
        "why_company", 
        "how_heard", 
        "referral",
        "previously_employed",
        "additional_info",
        "ai_policy_ack",
        "privacy_consent"
    }
    
    if key in CONTEXTUAL_KEYS or base in CONTEXTUAL_KEYS:
        return "CONTEXTUAL_APPLICATION_DECISION"
        
    # 4. General Policy
    if base in POLICY_KEYS or key in {"relocation_assistance_required"}:
        return "GENERAL_POLICY"
        
    # 5. Employer Form Field
    if key in CV_KEYS or base in CV_KEYS:
        return "STATIC_CANDIDATE_FACT"
        
    if key.startswith("custom."):
        if is_sensitive_key(key):
            return "STATIC_CANDIDATE_FACT"
        return "EMPLOYER_FORM_FIELD"
        
    # 6. Static Candidate Fact (default for CV keys, identity, education, etc.)
    return "STATIC_CANDIDATE_FACT" 

def intake_pass(key: str, field_type: str, category: str, question: str = "") -> str:
    cls = classify_card(key, field_type, category, question)
    if cls in ("INVALID_NOISE", "EMPLOYER_FORM_FIELD", "DOCUMENT", "CONTEXTUAL_APPLICATION_DECISION"):
        return "none"
    base = key.split(".")[0]
    if base in POLICY_KEYS:
        return "policy"
    if base in CV_KEYS or key in CV_KEYS:
        return "cv"
    if category == "story":
        return "story"
    return "gap"


def parse_report(path: Path = QUESTIONS_MD) -> list[CatalogEntry]:
    out: list[CatalogEntry] = []
    above = True
    for line in path.read_text(encoding="utf-8").splitlines():
        if "stopping line" in line:
            above = False
            continue
        m = _ROW.match(line)
        if not m:
            continue
        _, key, weighted, required, companies, _platforms, types, phrasings = m.groups()
        first_type = types.split(",")[0].strip() or "text"
        question = re.sub(r"\s*\(\d+\)$", "", phrasings.split(";")[0].strip())
        out.append(CatalogEntry(key, float(weighted) / 100, float(required) / 100, int(companies), first_type,
                                question, above))
    return out


def seed_catalog(session: Session, path: Path = QUESTIONS_MD) -> dict[str, int]:
    from autoapply.models.vault import VaultAnswer, VaultIdentity
    entries = [e for e in parse_report(path) if e.above_line or not e.key.startswith("custom.")]
    ident = session.query(VaultIdentity).first()
    existing = {a.canonical_key: a for a in session.query(VaultAnswer).all()}
    stats = {"seeded": 0, "kept": 0, "sensitive": 0}
    for e in entries:
        cat = category_of(e.key, e.field_type)
        sensitive = is_sensitive_key(e.key)
        stats["sensitive"] += sensitive
        row = existing.get(e.key)
        if row is None:
            row = VaultAnswer(canonical_key=e.key, answer=None, source="CATALOG", status="NEEDS REVIEW")
            session.add(row)
            stats["seeded"] += 1
        else:
            stats["kept"] += 1
        row.identity = row.identity or ident
        row.category, row.question = cat, e.question
        row.field_type, row.weight = e.field_type, e.weight
        row.intake_pass = intake_pass(e.key, e.field_type, cat, e.question) if (not row.answer or row.intake_pass == "gap") else row.intake_pass
        if sensitive:
            row.sensitivity = SENSITIVE
    session.commit()
    return stats
