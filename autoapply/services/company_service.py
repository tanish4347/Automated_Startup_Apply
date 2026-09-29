"""Company lookup and creation. Seeding the company universe lives in autoapply/discovery/."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from autoapply.logging import get_logger
from autoapply.models.company import ATS_TYPES, Company
from autoapply.services.dedup import normalize_company

log = get_logger(__name__)

def get_company(session: Session, name: str | None) -> Company | None:
    norm = normalize_company(name)
    if not norm:
        return None
    return session.query(Company).filter(Company.normalized_name == norm).first()


def get_or_create_company(
    session: Session, name: str | None, *, discovered_via: str | None = None, ats_type: str | None = None,
    meta: dict[str, Any] | None = None,
) -> Company | None:
    """Find a company by normalized name, creating it if new. Returns None for a blank name.

    `meta` is what a source knows about the employer (see SourceResult.company_meta); it only
    fills fields that are empty, except india_cities, which accumulates.
    """
    company = get_company(session, name)
    if company is None:
        norm = normalize_company(name)
        if not norm:
            return None
        company = Company(name=name.strip(), normalized_name=norm, discovered_via=discovered_via)
        session.add(company)
        session.flush()
    if ats_type in ATS_TYPES and company.ats_type in (None, "unknown"):
        company.ats_type = ats_type
    if meta:
        _apply_meta(session, company, meta)
    return company


def _apply_meta(session: Session, company: Company, meta: dict[str, Any]) -> None:
    domain = (meta.get("domain") or "").lower().removeprefix("www.") or None
    if domain and not company.domain:
        # domain is unique: two names for the same employer must not both claim it.
        if not session.query(Company.id).filter(Company.domain == domain).first():
            company.domain = domain
    if meta.get("careers_url") and not company.careers_url:
        company.careers_url = meta["careers_url"]
    if meta.get("about") and not company.about_text:
        company.about_text = meta["about"]
    if meta.get("is_india") and not company.is_india:
        company.is_india = True
    cities = [c for c in meta.get("india_cities") or [] if c]
    if cities:
        merged = list(company.india_cities or [])
        merged += [c for c in cities if c not in merged]
        if merged != (company.india_cities or []):
            company.india_cities = merged
