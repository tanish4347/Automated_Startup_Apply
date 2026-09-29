"""Application lifecycle service."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from autoapply.logging import get_logger
from autoapply.models.application import Application, ApplicationStatus

log = get_logger(__name__)

# Valid state transitions
_TRANSITIONS: dict[ApplicationStatus, set[ApplicationStatus]] = {
    ApplicationStatus.DISCOVERED: {ApplicationStatus.QUEUED, ApplicationStatus.CLOSED},
    ApplicationStatus.QUEUED: {
        ApplicationStatus.IN_PROGRESS,
        ApplicationStatus.FAILED,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.IN_PROGRESS: {
        ApplicationStatus.SUBMITTED,
        ApplicationStatus.FAILED,
        ApplicationStatus.PARKED,
        ApplicationStatus.CLOSED,
    },
    # Parked until the candidate enters the missing SENSITIVE answer, then re-queued.
    ApplicationStatus.PARKED: {ApplicationStatus.QUEUED, ApplicationStatus.CLOSED},
    ApplicationStatus.SUBMITTED: {
        ApplicationStatus.ASSESSMENT,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.OFFER,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.ASSESSMENT: {
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.REJECTED,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.INTERVIEW: {
        ApplicationStatus.REJECTED,
        ApplicationStatus.OFFER,
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.FAILED: {
        ApplicationStatus.QUEUED,  # retry
        ApplicationStatus.CLOSED,
    },
    ApplicationStatus.REJECTED: {ApplicationStatus.CLOSED},
    ApplicationStatus.OFFER: {ApplicationStatus.CLOSED},
    ApplicationStatus.CLOSED: set(),
}


def transition_status(
    session: Session,
    application: Application,
    new_status: ApplicationStatus,
    **kwargs: Any,
) -> Application:
    """Transition an application to a new status.

    Raises ValueError if the transition is not allowed.
    """
    current = application.status
    allowed = _TRANSITIONS.get(current, set())
    if new_status not in allowed:
        raise ValueError(
            f"Cannot transition from {current!r} to {new_status!r}. "
            f"Allowed: {allowed}"
        )

    application.status = new_status

    # Set date fields on certain transitions
    now = datetime.now(timezone.utc)
    if new_status == ApplicationStatus.QUEUED:
        application.date_queued = now
    elif new_status == ApplicationStatus.SUBMITTED:
        application.date_applied = now

    # Apply any extra fields
    for key, value in kwargs.items():
        if hasattr(application, key):
            setattr(application, key, value)

    session.commit()
    log.info(
        "application_status_changed",
        app_id=application.id,
        job_id=application.job_id,
        old_status=current.value,
        new_status=new_status.value,
    )
    return application


def queue_discovered(session: Session, limit: int = 50) -> list[Application]:
    """Move DISCOVERED applications to QUEUED status.

    Returns the list of newly queued applications.
    """
    apps = (
        session.query(Application)
        .filter(Application.status == ApplicationStatus.DISCOVERED)
        .order_by(Application.date_discovered.asc())
        .limit(limit)
        .all()
    )
    queued = []
    for app in apps:
        try:
            transition_status(session, app, ApplicationStatus.QUEUED)
            queued.append(app)
        except ValueError as e:
            log.warning("queue_transition_failed", app_id=app.id, error=str(e))
    return queued


def get_queued_applications(session: Session, limit: int = 10) -> list[Application]:
    """Get applications ready to be processed."""
    return (
        session.query(Application)
        .filter(Application.status == ApplicationStatus.QUEUED)
        .order_by(Application.date_queued.asc())
        .limit(limit)
        .all()
    )


def record_failure(
    session: Session,
    application: Application,
    error_message: str,
    error_details: dict | None = None,
) -> Application:
    """Record an application failure."""
    application.retry_count += 1
    return transition_status(
        session,
        application,
        ApplicationStatus.FAILED,
        error_message=error_message,
        error_details=error_details,
    )


def record_submission(
    session: Session,
    application: Application,
    *,
    resume_used: str | None = None,
    answers_submitted: dict | None = None,
    confirmation_id: str | None = None,
    confirmation_text: str | None = None,
    posting_snapshot: str | None = None,
) -> Application:
    """Record a successful application submission."""
    return transition_status(
        session,
        application,
        ApplicationStatus.SUBMITTED,
        resume_used=resume_used,
        answers_submitted=answers_submitted,
        confirmation_id=confirmation_id,
        confirmation_text=confirmation_text,
        posting_snapshot_at_apply=posting_snapshot,
    )


def get_application_stats(session: Session) -> dict[str, int]:
    """Get counts by status."""
    results = {}
    for status in ApplicationStatus:
        count = (
            session.query(Application)
            .filter(Application.status == status)
            .count()
        )
        results[status.value] = count
    results["total"] = sum(results.values())
    return results
