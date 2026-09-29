"""Job deduplication service."""

from __future__ import annotations

import hashlib
import re
from typing import Optional

from sqlalchemy.orm import Session

from autoapply.logging import get_logger
from autoapply.models.job import Job

log = get_logger(__name__)

def normalize_string(s: str | None) -> str:
    if not s:
        return ""
    s = s.lower()
    s = re.sub(r'[^a-z0-9]', '', s)
    return s

def compute_dedup_hash(
    title: str,
    company: str | None,
    location: str | None,
    source_id: str | None = None,
) -> str:
    """
    Cross-source deduplication strategy.
    """
    norm_title = normalize_string(title)
    norm_comp = normalize_string(company)
    
    # Bucket location simply
    loc_bucket = "unknown"
    if location:
        loc = location.lower()
        if "remote" in loc or "anywhere" in loc:
            loc_bucket = "remote"
        elif "india" in loc or "bengaluru" in loc or "bangalore" in loc:
            loc_bucket = "india"
        elif "us " in loc or "united states" in loc or "ca" in loc:
            loc_bucket = "us"
        else:
            loc_bucket = normalize_string(location)[:10]

    if source_id and not source_id.isdigit():
        # Only use source_id if it looks like a UUID or complex string, else collision risk
        if len(source_id) > 8:
            key = f"{norm_comp}::{source_id}"
            return hashlib.sha256(key.encode("utf-8")).hexdigest()
            
    key = f"{norm_comp}::{norm_title}::{loc_bucket}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()

def get_duplicate(session: Session, dedup_hash: str) -> Job | None:
    return session.query(Job).filter(Job.dedup_hash == dedup_hash).first()

def deduplicate_job(
    session: Session,
    title: str,
    company: str | None,
    location: str | None,
    source_id: str | None = None,
) -> tuple[str, Job | None]:
    h = compute_dedup_hash(title, company, location, source_id)
    dup = get_duplicate(session, h)
    return h, dup
