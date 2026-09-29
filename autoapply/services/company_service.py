"""Company lookup/creation and the career_pages.json seed import."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy.orm import Session

from autoapply.logging import get_logger
from autoapply.models.company import ATS_TYPES, PRIORITY_HIGH, PRIORITY_LOW, PRIORITY_NORMAL, Company
from autoapply.services.dedup import normalize_company

log = get_logger(__name__)

# Indian companies in career_pages.json (India-founded, or with the bulk of engineering in India).
INDIA_SEED = {
    "Razorpay", "CRED", "Groww", "Zerodha", "Meesho", "Swiggy", "Zepto", "Postman", "BrowserStack",
    "Zomato", "Flipkart", "Paytm", "Freshworks", "Chargebee", "Zoho", "Dream11", "ShareChat",
    "Upstox", "Ola", "Unacademy", "PhonePe", "Blinkit", "Dunzo", "Curefit", "Urban Company",
    "Lenskart", "PolicyBazaar", "Nykaa", "Udaan", "Cars24", "Dailyhunt", "Licious", "PharmEasy",
    "Pine Labs", "BharatPe", "Spinny", "NoBroker", "Acko", "Darwinbox", "Lead School",
    "Fractal Analytics", "Mu Sigma", "Tredence", "Tiger Analytics", "Observe.AI", "Mindtickle",
    "Zenoti", "Gupshup",
}

# The tail of career_pages.json came from a US ATS scrape. These entries are non-software
# industries (defense, aerospace, energy, pharma, insurance, trading, real estate, manufacturing)
# or tokens too generic to identify. Kept but deprioritised, never deleted. Software startups from
# the same scrape (Decagon, Sierra, Semgrep, Whatnot, incident.io, …) stay at normal priority.
OFF_TARGET_SEED = {
    "Spacex", "Andurilindustries", "Anavationllc", "Xcimer", "Shieldai", "Hermeus", "Saronic",
    "Allen-control-systems", "Northwoodspace", "The-exploration-company", "Raveaerospace",
    "Cesiumastro", "Antares", "Hadrian-automation", "Barnes", "Boschgroup", "Wabtec", "Averydennison",
    "Tmeic-corporation-americas", "Westerndigital", "Solidigm", "Rivianvw.tech",
    "Wabashvalleypoweralliance", "Rystad-energy", "Llnl", "Abbvie", "Wellmarkinc", "Protective",
    "Perryhomes", "Pilotcompany", "Belvederetrading", "Talos-trading", "Voleon", "Infinitequant",
    "Capula-investment-management-ltd", "Anthelioncap", "Evr", "Standtogether", "Telus-digital",
    "Rrsgroup", "Resultant", "Enfos-inc", "Apex-technology-inc",
    # Unidentifiable tokens
    "Sep", "Zip", "Ttp1", "Skyward1", "Twgai", "Cogna", "Melius", "Heliux", "Cyvl", "Flint", "Revel",
    "Oneapp", "Withpace", "Neighbor",
}


def get_company(session: Session, name: str | None) -> Company | None:
    norm = normalize_company(name)
    if not norm:
        return None
    return session.query(Company).filter(Company.normalized_name == norm).first()


def get_or_create_company(
    session: Session, name: str | None, *, discovered_via: str | None = None, ats_type: str | None = None,
) -> Company | None:
    """Find a company by normalized name, creating it if new. Returns None for a blank name."""
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
    return company


def import_career_pages(session: Session, path: Path) -> dict[str, int]:
    """Load career_pages.json into `companies`. Idempotent: existing rows are updated, not duplicated."""
    with open(path, "r", encoding="utf-8") as f:
        entries = json.load(f)

    stats = {"created": 0, "updated": 0, "india": 0, "low_priority": 0, "skipped_duplicates": 0}
    seen: set[str] = set()
    for entry in entries:
        name = (entry.get("company") or "").strip()
        norm = normalize_company(name)
        if not norm or norm in seen:
            stats["skipped_duplicates"] += 1
            continue
        seen.add(norm)

        company = get_company(session, name)
        if company is None:
            company = Company(name=name, normalized_name=norm)
            session.add(company)
            stats["created"] += 1
        else:
            stats["updated"] += 1

        company.ats_token = entry.get("board_token") or company.ats_token
        company.discovered_via = company.discovered_via or "career_pages_seed"
        company.is_india = name in INDIA_SEED
        if name in OFF_TARGET_SEED:
            company.priority = PRIORITY_LOW
        elif company.is_india:
            company.priority = PRIORITY_HIGH
        else:
            company.priority = PRIORITY_NORMAL
        stats["india"] += company.is_india
        stats["low_priority"] += company.priority == PRIORITY_LOW

    session.commit()
    log.info("career_pages_imported", **stats)
    return stats
