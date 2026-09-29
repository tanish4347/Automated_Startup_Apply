"""Job CRUD and query service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from autoapply.logging import get_logger
from autoapply.models.application import Application, ApplicationStatus
from autoapply.models.company import Company, JobSighting
from autoapply.models.job import Job, PayStatus, WorkMode
from autoapply.services.company_service import get_or_create_company
from autoapply.services.dedup import deduplicate_job
from autoapply.sources.http_client import guess_work_mode

log = get_logger(__name__)


# Fields copied from a later sighting when the stored job has no value for them.
_FILLABLE = (
    "description_raw", "description_text", "responsibilities", "requirements",
    "preferred_qualifications", "compensation_text", "source_url", "application_url", "location",
    "city", "country", "location_fit", "stipend_min", "stipend_max", "stipend_currency",
    "stipend_period", "duration_months", "role_family", "ats_platform", "company_info",
)


def _enum(enum_cls, value: str | None, default):
    try:
        return enum_cls((value or "").lower())
    except ValueError:
        return default


def record_sighting(session: Session, job: Job, source: str, source_url: str | None) -> JobSighting:
    """Add (or refresh) the (job, source, url) sighting."""
    url = source_url or ""
    sighting = next((s for s in job.sightings if s.source == source and s.source_url == url), None)
    now = datetime.now(timezone.utc)
    if sighting is None:
        sighting = JobSighting(job=job, source=source, source_url=url, first_seen_at=now, seen_at=now)
        session.add(sighting)
    else:
        sighting.seen_at = now
    return sighting


def _merge_into(existing: Job, data: dict[str, Any], company: Company | None) -> list[str]:
    """Fill fields the stored job is missing. Returns the names of fields that changed."""
    changed = []
    for field in _FILLABLE:
        new = data.get(field)
        if new not in (None, "", "unknown") and getattr(existing, field) in (None, "", "unknown"):
            setattr(existing, field, new)
            changed.append(field)
    if existing.company_id is None and company is not None:
        existing.company_id = company.id
        changed.append("company_id")
    if existing.work_mode in (None, WorkMode.UNKNOWN):
        mode = _enum(WorkMode, data.get("work_mode"), WorkMode.UNKNOWN)
        if mode != WorkMode.UNKNOWN:
            existing.work_mode = mode
            changed.append("work_mode")
    if existing.pay_status in (None, PayStatus.UNKNOWN):
        pay = _enum(PayStatus, data.get("pay_status"), PayStatus.UNKNOWN)
        if pay != PayStatus.UNKNOWN:
            existing.pay_status, existing.pay_evidence = pay, data.get("pay_evidence")
            changed.append("pay_status")
    # A direct ATS link beats a job-board link: it's the channel an applier can actually use.
    if data.get("apply_channel") == "ats_direct" and existing.apply_channel != "ats_direct":
        existing.apply_channel = "ats_direct"
        existing.ats_platform = data.get("ats_platform") or existing.ats_platform
        if data.get("application_url"):
            existing.application_url = data["application_url"]
            for app in existing.applications:
                if app.status == ApplicationStatus.DISCOVERED:
                    app.application_url = data["application_url"]
        changed.append("apply_channel")
    elif existing.apply_channel in (None, "unknown") and data.get("apply_channel") not in (None, "unknown"):
        existing.apply_channel = data["apply_channel"]
        changed.append("apply_channel")
    return changed


def ingest_job(session: Session, data: dict[str, Any]) -> Job | None:
    """Insert a job, or merge it into the stored copy and record where it was seen.

    Classification is not re-run on merge: a job first stored without a description keeps its
    original classification even when a later sighting fills the description in.
    """
    title = data.get("title", "")
    company = data.get("company")
    source = data.get("source", "unknown")
    location = data.get("location")

    if not title:
        log.warning("skipping_job_no_title", data=data)
        return None

    work_mode_str = data.get("work_mode") or "unknown"
    if work_mode_str == "unknown":
        work_mode_str = guess_work_mode(location)
    work_mode = _enum(WorkMode, work_mode_str, WorkMode.UNKNOWN)

    company_row = get_or_create_company(
        session, company, discovered_via=source, ats_type=data.get("ats_platform"),
        meta=data.get("company_meta"),
    )
    dedup_hash, existing_job = deduplicate_job(session, title, company, location, work_mode.value)
    expired = bool(data.get("expired"))
    now = datetime.now(timezone.utc)

    if existing_job:
        changed = _merge_into(existing_job, {**data, "work_mode": work_mode.value}, company_row)
        record_sighting(session, existing_job, source, data.get("source_url") or data.get("application_url"))
        existing_job.last_seen = now
        if expired and existing_job.closed_at is None:
            existing_job.closed_at, existing_job.is_active = now, 0
            changed.append("closed_at")
        session.commit()
        if changed:
            log.info("job_merged", job_id=existing_job.id, source=source, filled=changed)
        return existing_job

    cls_status = data.get("classification_status", "AUTO_REJECT")
    is_active = 1 if cls_status in ["AUTO_ACCEPT", "REVIEW"] and not expired else 0

    job = Job(
        source=source,
        source_id=data.get("source_id"),
        source_url=data.get("source_url"),
        application_url=data.get("application_url"),
        title=title,
        company=company,
        company_id=company_row.id if company_row else None,
        location=location,
        city=data.get("city"),
        country=data.get("country"),
        work_mode=work_mode,
        location_fit=data.get("location_fit"),
        description_raw=data.get("description_raw"),
        description_text=data.get("description_text"),
        responsibilities=data.get("responsibilities"),
        requirements=data.get("requirements"),
        preferred_qualifications=data.get("preferred_qualifications"),
        salary_min=data.get("salary_min"),
        salary_max=data.get("salary_max"),
        salary_currency=data.get("salary_currency"),
        compensation_text=data.get("compensation_text"),
        pay_status=_enum(PayStatus, data.get("pay_status"), PayStatus.UNKNOWN),
        pay_evidence=data.get("pay_evidence"),
        stipend_min=data.get("stipend_min"),
        stipend_max=data.get("stipend_max"),
        stipend_currency=data.get("stipend_currency"),
        stipend_period=data.get("stipend_period"),
        duration_months=data.get("duration_months"),
        role_family=data.get("role_family"),
        apply_channel=data.get("apply_channel"),
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
        is_active=is_active,
        closed_at=now if expired else None,
    )
    session.add(job)
    session.flush()
    record_sighting(session, job, source, data.get("source_url") or data.get("application_url"))

    # Jobs outside the location policy are kept and visible, but never auto-queued.
    if is_active and data.get("location_fit") != "outside_policy":
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
