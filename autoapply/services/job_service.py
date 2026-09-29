"""Job CRUD and query service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from autoapply.logging import get_logger
from autoapply.models.job import Job, PayStatus
from autoapply.models.application import Application, ApplicationStatus
from autoapply.services.dedup import deduplicate_job

log = get_logger(__name__)


def ingest_job(session: Session, data: dict[str, Any]) -> Job | None:
    title = data.get("title", "")
    company = data.get("company")
    source = data.get("source", "unknown")
    source_id = data.get("source_id")
    location = data.get("location")

    if not title:
        log.warning("skipping_job_no_title", data=data)
        return None

    dedup_hash, existing_job = deduplicate_job(session, title, company, location, source_id)
    
    if existing_job:
        existing_job.last_seen = datetime.now(timezone.utc)
        session.commit()
        return existing_job

    cls_status = data.get("classification_status", "AUTO_REJECT")
    is_active = 1 if cls_status in ["AUTO_ACCEPT", "REVIEW"] else 0

    pay_status_str = data.get("pay_status", "UNKNOWN").upper()
    try:
        pay_status = PayStatus(pay_status_str.lower())
    except ValueError:
        pay_status = PayStatus.UNKNOWN

    job = Job(
        source=source,
        source_id=source_id,
        source_url=data.get("source_url"),
        application_url=data.get("application_url"),
        title=title,
        company=company,
        location=location,
        description_raw=data.get("description_raw"),
        description_text=data.get("description_text"),
        responsibilities=data.get("responsibilities"),
        requirements=data.get("requirements"),
        preferred_qualifications=data.get("preferred_qualifications"),
        salary_min=data.get("salary_min"),
        salary_max=data.get("salary_max"),
        salary_currency=data.get("salary_currency"),
        compensation_text=data.get("compensation_text"),
        pay_status=pay_status,
        pay_evidence=data.get("pay_evidence"),
        posted_date=data.get("posted_date"),
        deadline=data.get("deadline"),
        ats_platform=data.get("ats_platform"),
        company_info=data.get("company_info"),
        posting_snapshot=data.get("posting_snapshot"),
        classification_category=data.get("classification_category"),
        classification_evidence=data.get("classification_evidence"),
        classification_status=cls_status,
        score=data.get("score"),
        reject_reason=data.get("reject_reason"),
        raw_data=data.get("raw_data"),
        dedup_hash=dedup_hash,
        tags=data.get("tags"),
        is_active=is_active
    )
    session.add(job)
    session.flush()

    if is_active:
        app = Application(
            job_id=job.id,
            company=company,
            role=title,
            status=ApplicationStatus.DISCOVERED,
            application_url=data.get("application_url"),
            date_discovered=datetime.now(timezone.utc),
        )
        session.add(app)
        
    session.commit()
    log.info("job_ingested", job_id=job.id, title=title, company=company, source=source, status=cls_status)
    return job

def get_job(session: Session, job_id: int) -> Job | None:
    return session.get(Job, job_id)

def list_jobs(
    session: Session,
    *,
    source: str | None = None,
    company: str | None = None,
    active_only: bool = True,
    limit: int = 100,
    offset: int = 0,
) -> list[Job]:
    query = session.query(Job)
    if active_only:
        query = query.filter(Job.is_active == 1)
    if source:
        query = query.filter(Job.source == source)
    if company:
        query = query.filter(Job.company.ilike(f"%{company}%"))
    return query.order_by(Job.discovered_at.desc()).offset(offset).limit(limit).all()

def count_jobs(session: Session, active_only: bool = True) -> int:
    query = session.query(Job)
    if active_only:
        query = query.filter(Job.is_active == 1)
    return query.count()
