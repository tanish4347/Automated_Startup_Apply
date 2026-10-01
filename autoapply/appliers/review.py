"""The candidate's decisions on parked attempts (/review). Runs as the candidate's own input.

pending tab  (review_only halts, missing answers): approve -> the application is re-queued; the
             next engine run re-fills it, and submits only if the platform's submit_mode is live.
             reject -> closed (the dedup claim is kept: you decided not to apply to this posting).
             edit -> your corrected values are written back (answer engine aliases / answers),
             then approve.
uncertain    (a live submit whose success assertion did not match): landed -> SUBMITTED;
             not landed -> re-queued.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from autoapply.models.application import Application, ApplicationStatus
from autoapply.models.attempt import PARKED, UNCERTAIN, ApplicationAttempt
from autoapply.models.job import Job


def items(session: Session, tab: str = "pending") -> list[dict[str, Any]]:
    outcome = UNCERTAIN if tab == "uncertain" else PARKED
    rows = (session.query(ApplicationAttempt, Job).join(Job, Job.id == ApplicationAttempt.job_id)
            .filter(ApplicationAttempt.outcome == outcome, ApplicationAttempt.review_status == "pending")
            .order_by(ApplicationAttempt.id).all())
    return [{
        "id": a.id, "outcome": a.outcome, "mode": a.mode, "park_reason": a.park_reason, "final_url": a.final_url,
        "screenshot": f"/review/evidence/{a.id}" if a.screenshot_path else None,
        "filled": a.filled_fields or [], "unfilled": a.unfilled_fields or [], "error": a.error,
        "job": {"id": j.id, "title": j.title, "company": j.company, "url": j.source_url, "channel": j.apply_channel,
                "location": j.location, "stipend": j.compensation_text},
    } for a, j in rows]


def decide(session: Session, attempt_id: int, action: str, values: dict[str, Any] | None = None) -> None:
    from autoapply.candidate.sensitive import user_input
    from autoapply.services.application_service import transition_status
    a = session.get(ApplicationAttempt, attempt_id)
    app = session.get(Application, a.application_id)
    now = datetime.now(timezone.utc)
    with user_input():
        if action == "edit":
            corrections = values or {}
            fields = [dict(f) for f in a.filled_fields or []]
            for f in fields:
                if f["label"] in corrections:
                    f["value"], f["tier"] = corrections[f["label"]], "review"
            for u in a.unfilled_fields or []:
                if u["label"] in corrections and corrections[u["label"]] not in (None, ""):
                    fields.append({"label": u["label"], "canonical_key": u.get("canonical_key"),
                                   "value": corrections[u["label"]], "tier": "review"})
            a.filled_fields = fields
            try:  # write back so the same question resolves at tier 1 next time
                from autoapply.answers.engine import record_correction
                for label, value in corrections.items():
                    if value not in (None, ""):
                        record_correction(session, label, value, platform=a.platform)
            except ImportError:
                pass
            action = "approve"
        if action == "approve":
            a.review_status = "approved"
            if app.status == ApplicationStatus.PARKED:
                transition_status(session, app, ApplicationStatus.QUEUED)
        elif action == "reject":
            a.review_status = "rejected"
            if app.status == ApplicationStatus.PARKED:
                transition_status(session, app, ApplicationStatus.CLOSED)
        elif action == "landed":           # uncertain tab: it did go through
            a.review_status, a.success_evidence = "approved", "confirmed by the candidate"
            transition_status(session, app, ApplicationStatus.SUBMITTED)
        elif action == "not_landed":
            a.review_status = "rejected"
            transition_status(session, app, ApplicationStatus.QUEUED)
        a.reviewed_at = now
        session.commit()
