"""The harness every applier runs in: modes, evidence, outcomes, caps, dedup claims, resume.

Modes (config/apply.yaml submit_mode, per platform; code only ever reads it):
  review_only  DEFAULT. The browser session is put in read-only mode (BrowserSession.read_only:
               every non-GET request is aborted before it leaves the browser, the same mechanism
               the question harvest uses, with a test proving a submit click sends nothing). The
               form is filled, screenshotted, and the attempt parks at the submit control for
               /review.
  live         read-only mode off. Only an explicit `submit_mode: live` in the config file enables it.

Every attempt records a full-page screenshot, the filled-field diff (canonical_key -> value placed
in the DOM), the final URL, and a form fingerprint (platform + sorted question set). Outcomes:
submitted (the platform's own success assertion matched), uncertain (no error, assertion did not
match: NOT success), parked (review_only halt, or a SENSITIVE / free-text answer missing), failed,
rerouted (the posting applies elsewhere: the job is re-tagged through the link resolver and
re-queued for whichever applier owns the new channel). "submitted" is never inferred from the
absence of an exception.

Platforms whose apply button IS the submission (Naukri: one click POSTs the application, see
docs/NAUKRI_FORM.md) set FormApplier.click_is_submit. There is no form to fill before the click,
so review_only stops before it: the posting is screenshotted and the attempt parks.

Never double-apply: an attempt first claims the job's dedup cluster (normalised company + title,
shared by every job row of one posting, e.g. the same internship on Internshala and Unstop) in
application_claims, whose UNIQUE constraint refuses a second claim.

Volume: daily_cap applications per UTC day (and optional per-platform caps), counted from
applications.last_attempt_at, with randomised pacing between attempts. Reaching a cap stops the run.
"""

from __future__ import annotations

import abc
import hashlib
import json
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable

import yaml
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from autoapply.config import PROJECT_ROOT
from autoapply.logging import get_logger
from autoapply.models.application import Application, ApplicationStatus
from autoapply.models.attempt import (
    FAILED, LIVE, PARKED, REROUTED, REVIEW_ONLY, SUBMITTED, UNCERTAIN, ApplicationAttempt, ApplicationClaim,
)
from autoapply.models.job import Job
from autoapply.services.dedup import normalize_brand, normalize_title

log = get_logger(__name__)

APPLY_CONFIG = PROJECT_ROOT / "config" / "apply.yaml"


# ── config (read-only) ───────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def load_config(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else APPLY_CONFIG
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def submit_mode(platform: str, cfg: dict[str, Any] | None = None) -> str:
    """review_only unless the config says exactly "live" for this platform."""
    cfg = load_config() if cfg is None else cfg
    return LIVE if (cfg.get("submit_mode") or {}).get(platform) == LIVE else REVIEW_ONLY


# ── never double-apply ───────────────────────────────────────────────────────

def cluster_key(job: Job) -> str:
    """Brand + normalised title: the same posting found on two sources shares it, whatever city,
    source or legal-vs-brand company spelling the job rows carry ("Acme Labs Pvt. Ltd." on
    Internshala, "ACME LABS" on Unstop). Over-merging here only ever skips an application."""
    return f"{normalize_brand(job.company)}|{normalize_title(job.title)}"


class DuplicateApplication(Exception):
    pass


def claim(session: Session, app: Application) -> None:
    """Take the dedup-cluster claim for this application, or raise DuplicateApplication."""
    key = app.dedup_cluster = app.dedup_cluster or cluster_key(app.job)
    existing = session.query(ApplicationClaim).filter_by(dedup_cluster=key).first()
    if existing is not None:
        if existing.application_id == app.id:
            return
        raise DuplicateApplication(f"already claimed by application {existing.application_id} ({key})")
    session.add(ApplicationClaim(dedup_cluster=key, application_id=app.id))
    try:
        session.flush()
    except IntegrityError:           # a concurrent run claimed it first
        session.rollback()
        raise DuplicateApplication(f"already claimed ({key})")


def release(session: Session, app: Application) -> None:
    session.query(ApplicationClaim).filter_by(application_id=app.id).delete()


# ── volume policy ────────────────────────────────────────────────────────────

class CapReached(Exception):
    pass


def utc_midnight(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def attempts_today(session: Session, platform: str | None = None, now: datetime | None = None) -> int:
    q = session.query(func.count(Application.id)).filter(Application.last_attempt_at >= utc_midnight(now))
    if platform:
        q = q.join(Job, Job.id == Application.job_id).filter(Job.apply_channel == platform)
    return int(q.scalar() or 0)


def check_caps(session: Session, platform: str, cfg: dict[str, Any] | None = None, now: datetime | None = None) -> None:
    cfg = load_config() if cfg is None else cfg
    global_cap = int(cfg.get("daily_cap", 40))
    plat_cap = int((cfg.get("platform_caps") or {}).get(platform, global_cap))
    if attempts_today(session, now=now) >= global_cap:
        raise CapReached(f"daily cap of {global_cap} applications reached")
    if attempts_today(session, platform, now=now) >= plat_cap:
        raise CapReached(f"{platform}: daily cap of {plat_cap} reached")


def pause(cfg: dict[str, Any] | None = None, n: int = 0, sleep: Callable[[float], None] = time.sleep,
          rng: random.Random | None = None) -> float:
    cfg = (load_config() if cfg is None else cfg).get("pacing") or {}
    rng = rng or random.Random()
    delay = rng.uniform(float(cfg.get("min_s", 45)), float(cfg.get("max_s", 120)))
    every = int(cfg.get("long_pause_every", 0) or 0)
    if every and n and n % every == 0:
        delay += rng.uniform(*(cfg.get("long_pause_s") or (300, 600)))
    sleep(delay)
    return delay


# ── resume ───────────────────────────────────────────────────────────────────

def resume_for(session: Session, role_family: str | None = None) -> str | None:
    """The single active vault_resume. role_family is the hook for per-role variants (not built)."""
    from autoapply.models.vault import VaultResume
    active = session.query(VaultResume).filter_by(is_active=True).order_by(VaultResume.id.desc()).first()
    return active.file_path if active else None


# ── appliers ─────────────────────────────────────────────────────────────────

@dataclass
class FormField:
    label: str
    field_type: str
    required: bool = False
    options: list[str] | None = None
    aa: str | None = None                 # data-aa id set by the form dump
    control: str | None = None            # input type / select / radio / checkbox
    option_aa: list[str] | None = None


@dataclass
class Answer:
    value: Any = None                     # None = not answered
    canonical_key: str | None = None
    tier: str | None = None               # "1a" alias | "1b" embedding | "2" entailment | None
    confidence: float = 0.0
    park_reason: str | None = None        # why it was not answered (SENSITIVE missing, free text ...)


Answerer = Callable[[FormField, Job], Answer]


class Reroute(Exception):
    """Raised by open_form when the posting applies somewhere else (a company site or an ATS).
    Raised before anything is sent; the harness re-tags the job and re-queues the application."""

    def __init__(self, url: str, note: str = ""):
        super().__init__(note or f"applies at {url}")
        self.url, self.note = url, note or f"applies at {url}"


class FormApplier(abc.ABC):
    """A harness applier. Subclasses open the form, and declare explicitly what proves a
    submission landed (success_assertion). Filling is generic over FormField."""

    platform: str = ""
    channels: tuple[str, ...] = ()
    click_is_submit = False   # True: the apply control itself submits; there is no form before it

    def can_handle(self, job: Job) -> bool:
        return (job.apply_channel or "") in self.channels

    @abc.abstractmethod
    def open_form(self, bs, job: Job) -> None:
        """Navigate to the job's application form (GETs only until submit)."""

    def read_fields(self, bs) -> list[FormField]:
        from autoapply.questions.platform_forms import FORM_DUMP_JS
        dump = bs.page.evaluate(FORM_DUMP_JS)
        out = []
        for f in dump.get("fields") or []:
            from autoapply.questions.harvest import clean_label
            label = clean_label(f.get("label"))
            if label:
                out.append(FormField(label, f.get("field_type") or "text", bool(f.get("required")),
                                     [o for o in f.get("options") or [] if o] or None, f.get("aa"), f.get("control"),
                                     f.get("option_aa")))
        return out

    def fill(self, bs, field: FormField, value: Any) -> None:
        page = bs.page
        if field.field_type == "file":
            page.locator(f'[data-aa="{field.aa}"]').set_input_files(str(value))
        elif field.control in ("radio", "checkbox") and field.options and field.option_aa:
            wanted = {str(v).strip().lower() for v in (value if isinstance(value, list) else [value])}
            for opt, aa in zip(field.options, field.option_aa):
                if opt.strip().lower() in wanted:
                    page.locator(f'[data-aa="{aa}"]').check()
        elif field.control == "select":
            page.locator(f'[data-aa="{field.aa}"]').select_option(label=str(value))
        elif field.control == "combobox":
            # react-select / autocomplete: type, then pick the option the widget offers for it
            box = page.locator(f'[data-aa="{field.aa}"]')
            box.click()
            box.fill(str(value))
            page.wait_for_timeout(800)
            opt = page.get_by_role("option", name=str(value), exact=False).first
            if opt.count():
                opt.click()
            else:
                box.press("Enter")
        else:
            page.locator(f'[data-aa="{field.aa}"]').fill(str(value))

    def resume_field(self, fields: list[FormField]) -> FormField | None:
        return next((f for f in fields if f.field_type == "file"), None)

    @abc.abstractmethod
    def submit_control(self, bs):
        """The submit button locator. Clicked only in live mode."""

    @abc.abstractmethod
    def success_assertion(self, bs, before_url: str) -> str | None:
        """Return evidence that the submission landed (confirmation text, URL change, a DOM
        node), or None. Anything short of the platform's explicit proof is uncertain."""


def fingerprint(platform: str, fields: list[FormField]) -> str:
    from autoapply.questions.harvest import normalize_label
    key = platform + "|" + "|".join(sorted(normalize_label(f.label) for f in fields))
    return hashlib.sha1(key.encode()).hexdigest()


# ── one attempt ──────────────────────────────────────────────────────────────

def _click_is_submit(session: Session, applier: FormApplier, app: Application, attempt: ApplicationAttempt,
                     bs, mode: str, cfg: dict[str, Any]) -> None:
    """The apply control is the submission. review_only: screenshot the posting and park before the
    click. live: click, then only the platform's success assertion makes it submitted."""
    from autoapply.services.application_service import transition_status
    shot = _evidence_dir(cfg) / f"attempt_{attempt.id}.png"
    attempt.form_fingerprint = fingerprint(applier.platform, [])
    if mode == REVIEW_ONLY:
        bs.page.screenshot(path=str(shot), full_page=True)
        attempt.screenshot_path, attempt.final_url = str(shot), bs.page.url
        attempt.outcome, attempt.review_status = PARKED, "pending"
        attempt.park_reason = (f"review_only: {applier.platform}'s Apply button is itself the submission; "
                               "halted before the click")
        transition_status(session, app, ApplicationStatus.PARKED)
        return
    before = bs.page.url
    applier.submit_control(bs).click()
    bs.page.wait_for_timeout(5000)
    evidence = applier.success_assertion(bs, before)
    bs.page.screenshot(path=str(shot), full_page=True)
    attempt.screenshot_path, attempt.final_url = str(shot), bs.page.url
    if evidence:
        attempt.outcome, attempt.success_evidence = SUBMITTED, evidence
        transition_status(session, app, ApplicationStatus.SUBMITTED)
    else:
        attempt.outcome, attempt.review_status = UNCERTAIN, "pending"
        transition_status(session, app, ApplicationStatus.PARKED)


@dataclass
class AttemptResult:
    attempt: ApplicationAttempt | None
    refused: str | None = None


def _evidence_dir(cfg: dict[str, Any]) -> Path:
    d = PROJECT_ROOT / (cfg.get("evidence_dir") or "data/evidence")
    d.mkdir(parents=True, exist_ok=True)
    return d


def run_attempt(session: Session, applier: FormApplier, app: Application, answerer: Answerer, *,
                browser_factory: Callable[[str], Any] | None = None, cfg: dict[str, Any] | None = None,
                now: datetime | None = None) -> AttemptResult:
    """One application attempt, end to end. Raises CapReached (stop the run)."""
    from autoapply.candidate.sensitive import ParkApplication
    from autoapply.services.application_service import transition_status
    cfg = load_config() if cfg is None else cfg
    now = now or datetime.now(timezone.utc)
    job = app.job
    check_caps(session, applier.platform, cfg, now)
    try:
        claim(session, app)
    except DuplicateApplication as e:
        app.error_message = f"not applied: {e}"
        transition_status(session, app, ApplicationStatus.CLOSED)
        log.info("duplicate_refused", app_id=app.id, reason=str(e))
        return AttemptResult(None, refused=str(e))

    mode = submit_mode(applier.platform, cfg)
    if app.status != ApplicationStatus.IN_PROGRESS:
        if app.status == ApplicationStatus.DISCOVERED:
            transition_status(session, app, ApplicationStatus.QUEUED)
        transition_status(session, app, ApplicationStatus.IN_PROGRESS)
    app.last_attempt_at = now
    attempt = ApplicationAttempt(application_id=app.id, job_id=job.id, platform=applier.platform, mode=mode,
                                 started_at=now)
    session.add(attempt)
    session.commit()

    filled: list[dict[str, Any]] = []
    unfilled: list[dict[str, Any]] = []
    submit_clicked = False
    bs = None
    try:
        from autoapply.sources.browser import BrowserSession
        bs = (browser_factory or (lambda p: BrowserSession(p)))(applier.platform).__enter__()
        if mode == REVIEW_ONLY:
            bs.read_only()
        applier.open_form(bs, job)
        if applier.click_is_submit:
            submit_clicked = mode == LIVE   # in live mode the click is the first thing it does
            _click_is_submit(session, applier, app, attempt, bs, mode, cfg)
            return AttemptResult(attempt)
        fields = applier.read_fields(bs)
        attempt.form_fingerprint = fingerprint(applier.platform, fields)
        resume_fld = applier.resume_field(fields)
        for fld in fields:
            if fld is resume_fld:
                path = resume_for(session, job.role_family)
                if path:
                    applier.fill(bs, fld, path)
                    filled.append({"label": fld.label, "canonical_key": "resume", "value": Path(path).name, "tier": "vault"})
                else:
                    unfilled.append({"label": fld.label, "reason": "no active resume in the vault", "required": fld.required})
                continue
            try:
                ans = answerer(fld, job)
            except ParkApplication as e:
                ans = Answer(park_reason=f"SENSITIVE missing: {e}")
            if ans.value is None:
                unfilled.append({"label": fld.label, "canonical_key": ans.canonical_key, "required": fld.required,
                                 "reason": ans.park_reason or "no answer", "field_type": fld.field_type,
                                 "options": fld.options})
                continue
            applier.fill(bs, fld, ans.value)
            filled.append({"label": fld.label, "canonical_key": ans.canonical_key, "value": ans.value,
                           "tier": ans.tier, "confidence": ans.confidence, "field_type": fld.field_type})
        shot = _evidence_dir(cfg) / f"attempt_{attempt.id}.png"
        bs.page.screenshot(path=str(shot), full_page=True)
        attempt.screenshot_path = str(shot)
        attempt.final_url = bs.page.url
        missing_required = [u for u in unfilled if u.get("required")]
        if missing_required or mode == REVIEW_ONLY:
            attempt.outcome = PARKED
            attempt.park_reason = ("; ".join(f"{u['label']}: {u['reason']}" for u in missing_required)
                                   if missing_required else "review_only: halted at the submit control")
            attempt.review_status = "pending"
            transition_status(session, app, ApplicationStatus.PARKED)
        else:
            before = bs.page.url
            submit_clicked = True
            applier.submit_control(bs).click()
            bs.page.wait_for_timeout(5000)
            evidence = applier.success_assertion(bs, before)
            attempt.final_url = bs.page.url
            attempt.screenshot_path = str(shot)
            bs.page.screenshot(path=str(shot), full_page=True)
            if evidence:
                attempt.outcome, attempt.success_evidence = SUBMITTED, evidence
                transition_status(session, app, ApplicationStatus.SUBMITTED)
            else:
                attempt.outcome, attempt.review_status = UNCERTAIN, "pending"
                transition_status(session, app, ApplicationStatus.PARKED)
    except CapReached:
        raise
    except Reroute as e:
        from autoapply.appliers.resolve import reroute
        reroute(job, e.url, e.note)
        attempt.outcome, attempt.park_reason, attempt.final_url = REROUTED, e.note[:500], e.url
        release(session, app)
        transition_status(session, app, ApplicationStatus.QUEUED)
        log.info("attempt_rerouted", app_id=app.id, to=e.url, channel=job.apply_channel)
    except Exception as e:
        attempt.error = f"{type(e).__name__}: {e}"[:2000]
        if submit_clicked:          # the click may have gone through: never call that a failure
            attempt.outcome, attempt.review_status = UNCERTAIN, "pending"
            transition_status(session, app, ApplicationStatus.PARKED)
        else:
            attempt.outcome = FAILED
            release(session, app)   # nothing was sent; the cluster may be tried again
            transition_status(session, app, ApplicationStatus.FAILED, error_message=attempt.error)
        log.warning("attempt_error", app_id=app.id, error=attempt.error)
    finally:
        attempt.filled_fields, attempt.unfilled_fields = filled, unfilled
        attempt.blocked_requests = list(getattr(bs, "blocked", []) or [])[:50] if bs else None
        attempt.finished_at = datetime.now(timezone.utc)
        session.commit()
        if bs is not None:
            bs.__exit__(None, None, None)
    log.info("attempt_done", app_id=app.id, platform=applier.platform, mode=mode, outcome=attempt.outcome,
             filled=len(filled), unfilled=len(unfilled))
    return AttemptResult(attempt)
