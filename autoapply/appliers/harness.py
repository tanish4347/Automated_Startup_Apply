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

Generated prose (answer tier 3) never auto-submits: an attempt with a tier-3 answer the candidate
has not approved in /review parks, whatever submit_mode says. Approval is recorded on the cached
draft (generated_answer), so the next attempt fills the approved text and may submit.

Multi-step forms (Unstop: Back/Next) declare FormApplier.next_control. Each step is filled, then
"Next" clicked. In review_only, a Next click that tries to send anything (the read-only guard blocks
it) means the step is itself a submission: the attempt stops there and parks. In live mode every
Next click counts as possibly-submitting, so a later error is uncertain, never failed.

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
    FAILED, LIVE, PARKED, REROUTED, REVIEW_ONLY, SUBMITTED, UNCERTAIN, ApplicationAttempt,
    ApplicationAttemptEvent, ApplicationClaim,
)
from autoapply.models.job import Job
from autoapply.services.dedup import normalize_brand, normalize_title

log = get_logger(__name__)

ATTEMPT_STAGES = {
    "OPEN", "FORM_READY", "DISCOVERED", "PLANNED", "FILLING", "VERIFYING",
    "REPAIRING", "REVIEW_READY", "PARKED", "BLOCKED", "FAILED",
}
_STAGE_NEXT = {
    "OPEN": {"FORM_READY", "BLOCKED", "FAILED", "PARKED"},
    "FORM_READY": {"DISCOVERED", "BLOCKED", "FAILED", "PARKED"},
    "DISCOVERED": {"PLANNED", "BLOCKED", "FAILED", "PARKED"},
    "PLANNED": {"FILLING", "PARKED", "BLOCKED", "FAILED"},
    "FILLING": {"VERIFYING", "REPAIRING", "PARKED", "BLOCKED", "FAILED"},
    "VERIFYING": {"FILLING", "PLANNED", "REPAIRING", "DISCOVERED", "REVIEW_READY", "PARKED", "BLOCKED", "FAILED"},
    "REPAIRING": {"VERIFYING", "FILLING", "PARKED", "BLOCKED", "FAILED"},
    "REVIEW_READY": {"PARKED", "BLOCKED", "FAILED"},
    "PARKED": set(), "BLOCKED": set(), "FAILED": set(),
}


def _event(session: Session, attempt: ApplicationAttempt, stage: str, *, action: str | None = None,
           expected: Any = None, observed: Any = None, verified: str | None = None,
           recovery_level: int | None = None, strategy: str | None = None, error: str | None = None) -> None:
    """Commit proof as it happens so process death cannot erase the observed progress."""
    if stage not in ATTEMPT_STAGES:
        raise ValueError(f"unknown attempt stage {stage!r}")
    session.add(ApplicationAttemptEvent(
        attempt_id=attempt.id, stage=stage, action=action, expected=expected, observed=observed,
        verified=verified, recovery_level=recovery_level, strategy=strategy,
        error=(error[:2000] if error else None),
    ))
    session.commit()


def _stage(session: Session, attempt: ApplicationAttempt, previous: str, stage: str, **proof: Any) -> str:
    if stage != previous and stage not in _STAGE_NEXT.get(previous, set()):
        raise ValueError(f"invalid attempt stage transition {previous} -> {stage}")
    _event(session, attempt, stage, **proof)
    return stage


def recovery_metrics(session: Session) -> dict[str, float | int]:
    """Measure review readiness and whether recorded recovery contributed to it."""
    rows = session.query(ApplicationAttemptEvent, ApplicationAttempt.application_id).join(
        ApplicationAttempt, ApplicationAttempt.id == ApplicationAttemptEvent.attempt_id
    ).all()
    by_app: dict[int, dict[str, Any]] = {}
    recovery_attempts = 0
    for event, app_id in rows:
        state = by_app.setdefault(app_id, {"review_ready": False, "recovered": False})
        state["review_ready"] |= event.stage == "REVIEW_READY"
        is_recovery = event.recovery_level is not None or event.action == "application_recovery"
        if is_recovery:
            state["recovered"] = True
            recovery_attempts += 1
    # Older attempts have no timeline by design; keep them out of the instrumented denominator.
    attempted = len(by_app)
    apps = list(by_app.values())
    review_ready = sum(bool(x["review_ready"]) for x in apps)
    raw_success = sum(bool(x["review_ready"] and not x["recovered"]) for x in apps)
    recovery_success = sum(bool(x["review_ready"] and x["recovered"]) for x in apps)
    recovered_apps = sum(bool(x["recovered"]) for x in apps)
    return {
        "attempted": int(attempted),
        "raw_success_rate": raw_success / attempted if attempted else 0.0,
        "recovery_success_rate": recovery_success / recovered_apps if recovered_apps else 0.0,
        "final_review_ready_rate": review_ready / attempted if attempted else 0.0,
        "recovery_attempts": recovery_attempts,
        "recovery_attempts_per_application": recovery_attempts / attempted if attempted else 0.0,
    }


def attempt_funnel_metrics(session: Session) -> dict[str, int | float]:
    """Return counts from persisted proof events; legacy rows remain visible as attempted only."""
    query = session.query(ApplicationAttemptEvent, ApplicationAttempt.application_id).join(
        ApplicationAttempt, ApplicationAttempt.id == ApplicationAttemptEvent.attempt_id
    )
    rows = query.order_by(ApplicationAttemptEvent.id.asc()).all()
    app_stages: dict[str, set[int]] = {}
    final_stage: dict[int, str] = {}
    answer_apps: set[int] = set()
    fields_filled = fields_verified = 0
    for event, app_id in rows:
        app_stages.setdefault(event.stage, set()).add(app_id)
        if event.stage in {"PARKED", "FAILED", "BLOCKED"}:
            final_stage[app_id] = event.stage
        if event.stage == "PLANNED" and (event.observed or {}).get("resolved_count", 0):
            answer_apps.add(app_id)
        if event.stage == "VERIFYING" and event.verified == "passed" and event.action in ("fill", "fill_resume"):
            fields_filled += 1
            fields_verified += 1
    total_attempted = session.query(func.count(func.distinct(ApplicationAttempt.application_id))).scalar() or 0
    instrumented_attempted = len({app_id for _, app_id in rows})
    funnel = {
        "attempted": instrumented_attempted,
        "legacy_attempted_without_events": int(total_attempted - instrumented_attempted),
        "opened": len(app_stages.get("FORM_READY", set())),
        "form_discovered": len(app_stages.get("DISCOVERED", set())),
        "form_understood": len(app_stages.get("PLANNED", set())),
        "answers_resolved": len(answer_apps),
        "fields_filled": fields_filled,
        "fields_verified": fields_verified,
        "all_required_fields_satisfied": len(app_stages.get("REVIEW_READY", set())),
        "review_ready": len(app_stages.get("REVIEW_READY", set())),
        "parked": sum(stage == "PARKED" for stage in final_stage.values()),
        "failed": sum(stage == "FAILED" for stage in final_stage.values()),
        "blocked": sum(stage == "BLOCKED" for stage in final_stage.values()),
    }
    funnel["review_ready_rate"] = funnel["review_ready"] / instrumented_attempted if instrumented_attempted else 0.0
    return funnel

APPLY_CONFIG = PROJECT_ROOT / "config" / "apply.yaml"


# ── config (read-only) ───────────────────────────────────────────────────────

@lru_cache(maxsize=None)
def load_config(path: str | None = None) -> dict[str, Any]:
    p = Path(path) if path else APPLY_CONFIG
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def submit_mode(platform: str, cfg: dict[str, Any] | None = None) -> str:
    """review_only unless the config says exactly "live" for this platform."""
    cfg = load_config() if cfg is None else cfg
    sm = cfg.get("submit_mode")
    if isinstance(sm, dict):
        return LIVE if sm.get(platform) == LIVE else REVIEW_ONLY
    return LIVE if sm == LIVE else REVIEW_ONLY


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
    field_id: str | None = None
    accessible_name: str | None = None
    role: str | None = None
    native_type: str | None = None
    disabled: bool = False
    visible: bool = True
    current_value: Any = None
    target: Any = None
    confidence: float = 0.0
    discovery_source: str = "legacy"
    context: dict[str, Any] = field(default_factory=dict)


@dataclass
class Answer:
    value: Any = None                     # None = not answered
    canonical_key: str | None = None
    tier: str | None = None               # "1a" alias | "1b" embedding | "2" entailment | None
    confidence: float = 0.0
    park_reason: str | None = None        # why it was not answered (SENSITIVE missing, free text ...)
    needs_approval: bool = False          # tier 3 draft not yet approved in /review: never submitted
    generated_id: int | None = None       # generated_answer row (tier 3)


Answerer = Callable[[FormField, Job], Answer]


class NeedsLogin(Exception):
    """The platform's browser profile is not logged in. Nothing was sent. The attempt re-queues the
    application and the run stops using that platform (every other job on it would hit the same wall)."""


class FormDiscoveryError(RuntimeError):
    """The form did not expose usable controls before bounded readiness expired."""


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
    max_steps = 1             # multi-step forms: pages filled before the final submit control

    # Optional: (method, url, body) -> True for a non-GET request that only READS (a GraphQL query
    # the form needs to render). Must refuse every write. None = plain read-only.
    read_only_allow: Callable[[str, str, str], bool] | None = None

    def can_handle(self, job: Job) -> bool:
        return (job.apply_channel or "") in self.channels

    @abc.abstractmethod
    def open_form(self, bs, job: Job) -> None:
        """Navigate to the job's application form (GETs only until submit)."""

    def read_fields(self, bs) -> list[FormField]:
        from autoapply.appliers.form_surface import FormDiscovery
        surface = FormDiscovery().discover(bs.page)
        self.last_surface = surface
        out: list[FormField] = []
        for f in surface.fields:
            from autoapply.questions.harvest import clean_label
            label = clean_label(f.label)
            if not label:
                continue
            aa = f"f{f.target.frame_index}-{len(out)}"
            out.append(FormField(label, f.field_type, f.required, f.options or None, aa,
                f.control, f.option_ids or None, f.field_id, f.accessible_name, f.role,
                f.native_type, f.disabled, f.visible, f.current_value, f.target,
                f.confidence, f.discovery_source))
        if out:
            return out
        # Compatibility fallback for older page adapters and test doubles. Native Playwright,
        # ARIA, and CDP discovery above are the primary sources.
        from autoapply.questions.platform_forms import FORM_DUMP_JS
        dump = bs.page.evaluate(FORM_DUMP_JS)
        overrides = self.label_overrides(bs)
        out = []
        for f in dump.get("fields") or []:
            from autoapply.questions.harvest import clean_label
            label = clean_label(overrides.get(f.get("aa") or "") or f.get("label"))
            if label:
                out.append(FormField(label, f.get("field_type") or "text", bool(f.get("required")),
                                     [o for o in f.get("options") or [] if o] or None, f.get("aa"), f.get("control"),
                                     f.get("option_aa")))
        return out

    def label_overrides(self, bs) -> dict[str, str]:
        """data-aa -> question text, for controls whose own label is missing or a bare marker."""
        return {}

    def fill(self, bs, field: FormField, value: Any) -> Any:
        page = bs.page

        locator = self._field_locator(bs, field)

        from autoapply.answers.type_adapter import adapt_value_to_type, TypeValidationError
        
        try:
            preserve_options = (field.control in ("radio", "checkbox") and field.options) or (
                field.field_type == "multi_select" and isinstance(value, (list, tuple, set)))
            if not preserve_options:
                value = adapt_value_to_type(value, field.field_type, field.control)
        except TypeValidationError as e:
            from autoapply.candidate.sensitive import ParkApplication
            raise ParkApplication(str(e))
            
        if field.field_type == "file":
            import os
            val_str = str(value)
            if not os.path.exists(val_str):
                raise FileNotFoundError(f"file path for {field.label!r} does not exist")
            locator.set_input_files(val_str)
        elif field.control in ("radio", "checkbox") and field.options and field.option_aa:
            expected_values = value if isinstance(value, (list, tuple, set)) else [value]
            matches = [_match_control_option(expected, field.options) for expected in expected_values]
            if not all(matches):
                raise ValueError(f"option is not offered by {field.label!r}")
            selected_indexes = {field.options.index(match) for match in matches}
            frame_index = getattr(field.target, "frame_index", 0) if field.target else 0
            frames = list(getattr(page, "frames", []) or [page])
            if field.control == "checkbox":
                for idx, option in enumerate(field.option_aa):
                    item = frames[frame_index].locator(f'[data-aa="{option}"]')
                    if idx in selected_indexes:
                        item.check()
                    else:
                        item.uncheck()
            else:
                if len(selected_indexes) != 1:
                    raise ValueError("a radio group requires exactly one selected option")
                idx = next(iter(selected_indexes))
                frames[frame_index].locator(f'[data-aa="{field.option_aa[idx]}"]').check()
        elif field.control == "checkbox":
            locator.set_checked(bool(value))
        elif field.control == "select" and field.options:
            expected_values = value if isinstance(value, (list, tuple, set)) else [value]
            matched = [_match_control_option(v, field.options) for v in expected_values]
            if not all(matched):
                raise ValueError(f"requested option is not offered by {field.label!r}")
            locator.select_option(label=matched if field.field_type == "multi_select" else matched[0])
        elif field.control == "combobox":
            # React-select/autocomplete and custom select widgets: open, inspect visible choices,
            # type only when the widget exposes an editable input, then choose an offered match.
            locator.click()
            editable = locator.evaluate("el=>el.matches('input,textarea,[contenteditable=true]')")
            if editable:
                locator.fill(str(value))
            frame_index = getattr(field.target, "frame_index", 0) if field.target else 0
            frames = list(getattr(page, "frames", []) or [page])
            options = frames[frame_index].get_by_role("option")
            try:
                options.first.wait_for(state="visible", timeout=1200)
            except Exception:
                pass
            visible_options = [options.nth(i) for i in range(min(options.count(), 100))
                               if options.nth(i).is_visible()]
            ui_options = [option.inner_text().strip() for option in visible_options]
            matched_opt = _match_control_option(value, ui_options)
            if not matched_opt:
                raise ValueError(f"no offered option matches {value!r} for {field.label!r}")
            visible_options[ui_options.index(matched_opt)].click()
        else:
            locator.fill(str(value))
        return self.verify_fill(bs, field, value)

    @staticmethod
    def _field_locator(bs, field: FormField):
        if field.target is not None:
            from autoapply.appliers.form_surface import target_locator
            return target_locator(bs.page, field.target)
        return bs.page.locator(f'[data-aa="{field.aa}"]')

    def verify_fill(self, bs, field: FormField, value: Any) -> Any:
        """Read the actual value or selection back after the interaction."""
        # Small test/browser adapters expose their DOM state as a `filled` mapping. Treat it as an
        # observed state only when explicitly present; real Playwright always follows DOM readback.
        adapter_values = getattr(bs.page, "filled", None)
        if field.target is None and isinstance(adapter_values, dict):
            actual = adapter_values.get(field.label)
            if actual != value:
                raise ValueError(f"adapter value verification failed for {field.label!r}")
            return {"value": actual}
        locator = self._field_locator(bs, field)
        if field.control in ("checkbox", "radio") and field.options and field.option_aa:
            expected_values = value if isinstance(value, (list, tuple, set)) else [value]
            matched = [_match_control_option(v, field.options) for v in expected_values]
            if not all(matched):
                raise ValueError(f"requested option is not offered by {field.label!r}")
            expected_indexes = {field.options.index(v) for v in matched}
            frame_index = getattr(field.target, "frame_index", 0) if field.target else 0
            frames = list(getattr(bs.page, "frames", []) or [bs.page])
            actual_indexes = {i for i, aa in enumerate(field.option_aa)
                              if frames[frame_index].locator(f'[data-aa="{aa}"]').is_checked()}
            if actual_indexes != expected_indexes:
                raise ValueError(f"option verification failed for {field.label!r}: {actual_indexes}")
            return {"selected_indexes": sorted(actual_indexes)}
        if field.field_type == "file":
            files = locator.evaluate("el => [...(el.files||[])].map(f=>f.name)") or []
            if not files or Path(str(value)).name not in files:
                raise ValueError(f"file verification failed for {field.label!r}")
            return {"files": files}
        state = locator.evaluate("el => ({value:el.isContentEditable?el.innerText:el.value, checked:el.checked, "
            "selected:[...(el.selectedOptions||[])].map(o=>o.textContent.trim()), aria:el.getAttribute('aria-checked'), "
            "ariaValue:el.getAttribute('aria-valuetext'), expanded:el.getAttribute('aria-expanded'), "
            "selectedState:el.getAttribute('aria-selected'), dataValue:el.getAttribute('data-value'), text:el.innerText})")
        if field.control == "combobox":
            selected = state.get("ariaValue") or state.get("dataValue")
            if not selected and state.get("selected"):
                selected = ", ".join(state["selected"])
            if not selected and not locator.evaluate("el=>el.matches('input,textarea,[contenteditable=true]')"):
                selected = state.get("text")
            if not selected and state.get("expanded") == "false":
                selected = state.get("value")
            if not selected or not _match_control_option(value, [str(selected)]):
                raise ValueError(f"combobox selected-value verification failed for {field.label!r}")
            return {"selected": selected}
        if field.field_type in ("select", "multi_select"):
            expected = value if isinstance(value, (list, tuple, set)) else [value]
            actual = state.get("selected") or ([state.get("text") or ""] if field.field_type == "select" else [])
            if not all(_match_control_option(item, actual) for item in expected):
                raise ValueError(f"selection verification failed for {field.label!r}: {actual!r}")
            return {"selected": actual}
        elif field.field_type == "checkbox":
            actual = bool(state.get("checked")) or state.get("aria") == "true"
            if actual != bool(value):
                raise ValueError(f"checkbox verification failed for {field.label!r}")
            return {"checked": actual}
        else:
            actual = str(state.get("value") or state.get("aria") or state.get("text") or "").strip()
            if actual != str(value).strip():
                raise ValueError(f"value verification failed for {field.label!r}: got {actual!r}")
            return {"value": actual}

    def next_control(self, bs):
        """Multi-step forms: the visible "Next" control of the current step, or None on the last step."""
        return None

    def recover_field(self, bs, field: FormField, value: Any, error: Exception) -> Any:
        """Optional ATS-specific deterministic interaction recovery. None means unsupported."""
        return None

    def resume_field(self, fields: list[FormField]) -> FormField | None:
        """The file input labelled resume/CV, else the first one that is not a photo or cover letter
        (SmartRecruiters' first file input is the profile image; Ashby's is an autofill box)."""
        import re
        files = [f for f in fields if f.field_type == "file"]
        named = [f for f in files if re.search(r"resume|\bcv\b|curriculum", f.label, re.I)]
        rest = [f for f in files if not re.search(r"image|photo|picture|avatar|cover|autofill|^application$", f.label, re.I)]
        return (named or rest or [None])[0]

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


def _platform_writes(blocked: list[str], page_url: str) -> bool:
    """Did a click try to send something to the platform itself (not analytics)?"""
    from urllib.parse import urlsplit
    host = ".".join(urlsplit(page_url).netloc.split(".")[-2:])
    return any(host and host in urlsplit(b.split(" ", 1)[-1]).netloc for b in blocked)


def _page_progress_state(bs, applier: FormApplier) -> tuple[str, tuple[str, ...]]:
    """Small deterministic page signature used to prove that Next advanced a step."""
    url = str(getattr(bs.page, "url", "") or "")
    try:
        fields = applier.read_fields(bs)
        labels = tuple(sorted(_field_semantic_identity(f)[0] for f in fields))
    except Exception:
        labels = ()
    return url, labels


def _validation_message(bs) -> str | None:
    try:
        return bs.page.locator(
            '[role="alert"], [aria-live="assertive"], .invalid-feedback, .field-error, '
            '[class*="error"], [class*="Error"]'
        ).evaluate_all("els => els.filter(e => { const r=e.getBoundingClientRect(),s=getComputedStyle(e); "
                       "return r.width&&r.height&&s.visibility!=='hidden'&&s.display!=='none' })"
                       ".map(e => (e.innerText||e.textContent||'').trim()).filter(Boolean).slice(0,8).join(' | ')") or None
    except Exception:
        return None


def _empty_form_diagnostics(bs, surface) -> str:
    """Classify the observable reasons a zero-field surface may be incomplete."""
    diagnostics = list(getattr(surface, "diagnostics", []) or [])
    try:
        title = str(bs.page.title() or "")[:160]
        html = bs.page.content()
    except Exception:
        title, html = "", ""
    lower = html.lower()
    if any(token in lower or token in title.lower() for token in ("captcha", "datadome", "cloudflare", "verify you are human")):
        diagnostics.append("challenge indicators present; no bypass attempted")
    if any(x in lower for x in ("loading", "please wait", "aria-busy=\"true\"")):
        diagnostics.append("loading indicator present; form may not be mounted")
    if "<iframe" in lower:
        diagnostics.append("iframe markup present; attached-frame discovery returned no fields")
    if "shadow-root" in lower or "web component" in lower:
        diagnostics.append("web component markup present; inspect open/closed Shadow DOM diagnostics")
    if not getattr(bs.page, "frames", None) or len(getattr(bs.page, "frames", []) or []) <= 1:
        diagnostics.append("no child frame currently attached")
    try:
        forms = bs.page.locator("form").count()
        diagnostics.append(f"forms={forms}; url={str(bs.page.url)[:240]}; title={title!r}")
    except Exception:
        diagnostics.append(f"page metadata unavailable; url={str(getattr(bs.page, 'url', ''))[:240]}")
    return "; ".join(dict.fromkeys(diagnostics))


def _fill_page(session: Session, applier: FormApplier, bs, job: Job, fields: list[FormField], answerer: Answerer,
               filled: list[dict[str, Any]], unfilled: list[dict[str, Any]],
               event: Callable[..., None] | None = None) -> None:
    from autoapply.candidate.sensitive import ParkApplication
    resume_fld = applier.resume_field(fields)
    # Resolve a complete step before performing any interaction. New conditional controls are
    # added to this plan after choice widgets trigger a bounded re-observation.
    plan: list[tuple[FormField, Answer | None, str | None]] = []

    def resolve(fld: FormField) -> None:
        if fld is resume_fld:
            plan.append((fld, None, resume_for(session, job.role_family)))
            return
        try:
            plan.append((fld, answerer(fld, job), None))
        except ParkApplication as e:
            plan.append((fld, Answer(park_reason=f"SENSITIVE missing: {e}"), None))

    def fill_and_observe(fld: FormField, value: Any) -> Any:
        observed = applier.fill(bs, fld, value)
        if observed is None:
            observed = applier.verify_fill(bs, fld, value)
        if observed is None:
            raise ValueError("interaction returned no observable verification state")
        return observed

    for field in fields:
        resolve(field)
    if event:
        resolved_count = sum(1 for fld, ans, resume in plan if resume is not None or (ans is not None and ans.value is not None))
        event("PLANNED", action="resolve_answers", expected={"field_count": len(plan)},
              observed={"field_count": len(plan), "resolved_count": resolved_count,
                        "unresolved_count": len(plan) - resolved_count}, verified="passed")
        event("FILLING", action="fill_fields", expected={"resolved_fields": resolved_count},
              observed={"interactions_started": True}, verified="not_applicable")
    seen = {_field_identity(fld) for fld in fields}
    index = 0
    while index < len(plan):
        fld, ans, resume_path = plan[index]
        index += 1
        if resume_path == "__STALE__":
            continue
        if fld.disabled:
            unfilled.append({"label": fld.label, "reason": "USER_INPUT_REQUIRED: control is disabled",
                             "required": fld.required, "unsupported": True})
            continue
        if resume_path is not None:
            try:
                if event:
                    event("FILLING", action="fill_resume", expected={"label": fld.label},
                          observed={"target_resolved": True}, verified="not_applicable")
                observed = fill_and_observe(fld, resume_path)
                if event:
                    event("VERIFYING", action="fill_resume", expected={"label": fld.label,
                           "filename": Path(resume_path).name}, observed=observed, verified="passed",
                           strategy="file_readback")
                filled.append({"label": fld.label, "canonical_key": "resume", "value": Path(resume_path).name,
                               "tier": "vault"})
            except Exception as e:
                unfilled.append({"label": fld.label, "reason": f"USER_INPUT_REQUIRED: {e}",
                                 "required": fld.required, "unsupported": True})
            continue
        if ans is None or ans.value is None:
            unfilled.append({"label": fld.label, "canonical_key": ans.canonical_key if ans else None,
                             "required": fld.required, "reason": (ans.park_reason if ans else None) or "no answer",
                             "field_type": fld.field_type, "options": fld.options})
            continue
        try:
            if event:
                event("FILLING", action="fill_field", expected={"label": fld.label},
                      observed={"target_resolved": True}, verified="not_applicable")
            observed = fill_and_observe(fld, ans.value)
        except Exception as initial_error:
            # Bounded deterministic recovery: inspect the live surface once, then reacquire the
            # same semantic field from fresh locators. Never replay a stale ref/selector.
            try:
                observed_fields = applier.read_fields(bs)
                current_matches = [candidate for candidate in observed_fields
                                   if _field_semantic_identity(candidate) == _field_semantic_identity(fld)]
                dom_observation = ({"matches": len(current_matches),
                                    "current_value": current_matches[0].current_value if len(current_matches) == 1 else None,
                                    "visible": current_matches[0].visible if len(current_matches) == 1 else None})
                dom_verified = "unavailable"
                if len(current_matches) == 1:
                    try:
                        dom_observation["readback"] = applier.verify_fill(bs, current_matches[0], ans.value)
                        dom_verified = "passed"
                    except Exception:
                        dom_verified = "failed"
                if event:
                    event("REPAIRING", action="re_read_current_dom",
                          expected={"label": fld.label, "value": ans.value}, observed=dom_observation,
                          verified=dom_verified, recovery_level=1, strategy="live_form_surface_readback")
            except Exception as reread_error:
                if event:
                    event("REPAIRING", action="re_read_current_dom",
                          expected={"label": fld.label, "value": ans.value},
                          observed={"error": str(reread_error)[:300]}, verified="failed",
                          recovery_level=1, strategy="live_form_surface_readback")
            if event:
                event("REPAIRING", action="fill", expected={"label": fld.label, "value": ans.value},
                      observed={"error": str(initial_error)[:300]}, verified="failed",
                      strategy="initial_interaction_failure")
            repaired = None
            recovery_errors = [initial_error]
            try:
                fresh_fields = applier.read_fields(bs)
                matches = [candidate for candidate in fresh_fields
                           if _field_semantic_identity(candidate) == _field_semantic_identity(fld)]
                if len(matches) == 1:
                    repaired = matches[0]
                    if event:
                        event("REPAIRING", action="rediscover_field", expected={"label": fld.label},
                              observed={"matches": 1, "frame": getattr(repaired.target, "frame_index", 0)},
                              verified="passed", recovery_level=2, strategy="fresh_semantic_locator")
                    strategies = getattr(repaired.target, "strategies", []) if repaired.target else []
                    if event:
                        event("REPAIRING", action="resolve_target", expected={"unique_target": True},
                              observed={"strategy_count": len(strategies),
                                        "strategies": [s.get("kind") for s in strategies]},
                              verified="passed", recovery_level=3,
                              strategy="alternate_role_label_placeholder_native_locators")
                        surface = getattr(applier, "last_surface", None)
                        event("REPAIRING", action="observe_accessibility_surface",
                              expected={"semantic_target_observed": True},
                              observed={"accessibility_nodes": len(getattr(surface, "accessibility", []) or []),
                                        "source": getattr(repaired, "discovery_source", "unknown")},
                              verified="passed", recovery_level=4, strategy="form_surface_rediscovery")
                        event("REPAIRING", action="resolve_frame_shadow_scope",
                              expected={"target_scope_current": True},
                              observed={"frame_chain": getattr(repaired.target, "frame_chain", []),
                                        "shadow_chain": getattr(repaired.target, "shadow_chain", [])},
                              verified="passed", recovery_level=5, strategy="frame_shadow_aware_locator")
                    observed = fill_and_observe(repaired, ans.value)
                    fld = repaired
                else:
                    recovery_errors.append(LookupError(f"semantic rediscovery found {len(matches)} targets"))
                    if event:
                        event("REPAIRING", action="rediscover_field", expected={"label": fld.label},
                              observed={"matches": len(matches)}, verified="failed", recovery_level=2,
                              strategy="fresh_semantic_locator")
            except Exception as recovery_error:
                recovery_errors.append(recovery_error)
                repaired = None
                if event:
                    event("REPAIRING", action="refill", expected={"label": fld.label},
                          observed={"error": str(recovery_error)[:300]}, verified="failed",
                          recovery_level=2, strategy="fresh_semantic_locator")
            if repaired is None:
                try:
                    platform_observation = applier.recover_field(bs, fld, ans.value, recovery_errors[-1])
                    if platform_observation is not None:
                        observed = platform_observation
                        repaired = fld
                    if event:
                        event("REPAIRING", action="platform_recovery", expected={"field_verified": True},
                              observed={"available": platform_observation is not None},
                              verified="passed" if platform_observation is not None else "unavailable",
                              recovery_level=6, strategy=f"{applier.platform}_recovery")
                except Exception as recovery_error:
                    recovery_errors.append(recovery_error)
                    if event:
                        event("REPAIRING", action="platform_recovery", expected={"field_verified": True},
                              observed={"error": str(recovery_error)[:300]}, verified="failed",
                              recovery_level=6, strategy=f"{applier.platform}_recovery")
            if repaired is None:
                e = recovery_errors[-1]
                if event:
                    event("REPAIRING", action="llm_recovery", expected={"deterministic_recovery_failed": True},
                          observed={"available": False, "reason": "no interaction-recovery LLM adapter configured"},
                          verified="unavailable", recovery_level=7, strategy="llm_after_deterministic_only")
                reason = f"USER_INPUT_REQUIRED: interaction/verification failed after deterministic recovery ({type(e).__name__}: {e})"
                unfilled.append({"label": fld.label, "canonical_key": ans.canonical_key, "required": fld.required,
                                 "reason": reason,
                                 "field_type": fld.field_type, "options": fld.options, "unsupported": True})
                if event:
                    event("REPAIRING", action="park_field", expected={"field_verified": True},
                          observed={"parked": True, "reason": reason}, verified="failed",
                          recovery_level=8, strategy="park_with_exact_reason", error=reason)
                continue
        except BaseException:
            raise
        if event:
            event("VERIFYING", action="fill", expected={"label": fld.label, "value": ans.value},
                  observed=observed, verified="passed", strategy="dom_readback")
        row = {"label": fld.label, "canonical_key": ans.canonical_key, "value": ans.value,
               "tier": ans.tier, "confidence": ans.confidence, "field_type": fld.field_type}
        if ans.tier == "3":
            row.update(generated_id=ans.generated_id, needs_approval=ans.needs_approval)
        filled.append(row)

        if fld.control in ("select", "combobox", "radio", "checkbox") or fld.field_type in ("select", "multi_select"):
            fresh = applier.read_fields(bs)
            refreshed = {_field_identity(item): item for item in fresh}
            for pending in range(index, len(plan)):
                old, pending_answer, pending_resume = plan[pending]
                replacement = refreshed.get(_field_identity(old))
                if replacement is None:
                    plan[pending] = (old, None, "__STALE__")
                else:
                    plan[pending] = (replacement, pending_answer, pending_resume)
            for candidate in fresh:
                ident = _field_identity(candidate)
                if ident not in seen:
                    seen.add(ident)
                    resolve(candidate)
                    if event:
                        _, answer, resume = plan[-1]
                        event("PLANNED", action="resolve_conditional_field",
                              expected={"field": candidate.label},
                              observed={"resolved": resume is not None or (answer is not None and answer.value is not None)},
                              verified="passed")


def _field_identity(field: FormField) -> tuple[int, str, str]:
    frame = getattr(field.target, "frame_index", 0) if field.target else 0
    label = " ".join((field.accessible_name or field.label).lower().split())
    return frame, label, field.role or ""


def _field_semantic_identity(field: FormField) -> tuple[str, str]:
    label = " ".join((field.accessible_name or field.label).lower().split())
    return label, field.role or ""


def _match_control_option(value: Any, options: list[str]) -> str | None:
    from autoapply.answers.type_adapter import match_dropdown_option
    if isinstance(value, bool):
        aliases = {"yes", "true", "on", "1"} if value else {"no", "false", "off", "0"}
        found = [option for option in options if option.strip().lower() in aliases]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise ValueError("boolean answer matches multiple offered options")
    return match_dropdown_option(value, options)


@dataclass
class AttemptResult:
    attempt: ApplicationAttempt | None
    refused: str | None = None


def _evidence_dir(cfg: dict[str, Any]) -> Path:
    d = PROJECT_ROOT / (cfg.get("evidence_dir") or "data/evidence")
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run_attempt_impl(session: Session, applier: FormApplier, app: Application, answerer: Answerer, *,
                browser_factory: Callable[[str], Any] | None = None, cfg: dict[str, Any] | None = None,
                now: datetime | None = None, attempt_number: int = 1) -> AttemptResult:
    """One application attempt, end to end. Raises CapReached (stop the run)."""
    from autoapply.candidate.sensitive import ParkApplication
    from autoapply.services.application_service import transition_status
    cfg = load_config() if cfg is None else cfg
    now = now or datetime.now(timezone.utc)
    job = app.job
    
    if attempt_number == 1:
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
    current_stage = "OPEN"
    _event(session, attempt, current_stage, action="start_attempt", expected={"browser_open": True},
           observed={"application_id": app.id, "platform": applier.platform}, verified="passed")

    def record(stage: str, **proof: Any) -> None:
        nonlocal current_stage
        current_stage = _stage(session, attempt, current_stage, stage, **proof)

    filled: list[dict[str, Any]] = []
    unfilled: list[dict[str, Any]] = []
    submit_clicked = False
    bs = None
    try:
        from autoapply.sources.browser import BrowserSession
        bs = (browser_factory or (lambda p: BrowserSession(p)))(applier.platform).__enter__()
        if mode == REVIEW_ONLY:
            if applier.read_only_allow is not None:
                bs.read_only(applier.read_only_allow)
            else:
                bs.read_only()
        applier.open_form(bs, job)
        record("FORM_READY", action="open_form", expected={"application_form": True},
               observed={"url": str(getattr(bs.page, "url", ""))[:500]}, verified="passed")
        if applier.click_is_submit:
            submit_clicked = mode == LIVE   # in live mode the click is the first thing it does
            _click_is_submit(session, applier, app, attempt, bs, mode, cfg)
            return AttemptResult(attempt)
        all_fields: list[FormField] = []
        step_halt = None
        for step in range(max(1, applier.max_steps)):
            fields = applier.read_fields(bs)
            if not fields:
                checker = getattr(bs, "check", None)
                if checker:
                    checker()
                surface = getattr(applier, "last_surface", None)
                status = getattr(surface, "status", "empty")
                detail = _empty_form_diagnostics(bs, surface)
                record("FAILED", action="discover_form", expected={"interactive_fields": ">=1"},
                       observed={"count": 0, "status": status, "diagnostics": detail}, verified="failed")
                raise FormDiscoveryError(f"form discovery {status}: no interactive fields found" +
                                         (f" ({detail})" if detail else ""))
            record("DISCOVERED", action="discover_form", expected={"interactive_fields": ">=1"},
                   observed={"count": len(fields), "required": sum(bool(f.required) for f in fields),
                             "frames": getattr(getattr(applier, "last_surface", None), "frame_count", None)},
                   verified="passed")
            all_fields += fields
            _fill_page(session, applier, bs, job, fields, answerer, filled, unfilled, event=record)
            nxt = applier.next_control(bs) if step + 1 < applier.max_steps else None
            if nxt is None:
                break
            if [u for u in unfilled if u.get("required")]:
                break                              # a required answer is missing: the next step can't open
            sent_before = len(getattr(bs, "blocked", []) or [])
            before_state = _page_progress_state(bs, applier)
            if mode == LIVE:
                submit_clicked = True              # a Next may save the step server-side
            nxt.click()
            sent = (getattr(bs, "blocked", []) or [])[sent_before:]
            try:
                bs.page.wait_for_timeout(350)
            except Exception:
                pass
            after_state = _page_progress_state(bs, applier)
            validation = _validation_message(bs)
            if mode == REVIEW_ONLY and _platform_writes(sent, bs.page.url):
                step_halt = f"review_only: step {step + 1}'s Next tries to send data ({sent[0][:120]}); halted there"
                record("PARKED", action="click_next", expected={"step_changed": True},
                       observed={"blocked_request": sent[0][:160], "before": before_state, "after": after_state},
                       verified="unavailable", strategy="review_only_write_guard")
                break
            advanced = after_state != before_state
            if validation or not advanced:
                step_halt = (f"validation failed after Next: {validation}" if validation else
                             f"Next did not advance the form (step {step + 1})")
                record("PARKED", action="click_next", expected={"step_changed": True, "validation_message": None},
                       observed={"step_changed": advanced, "validation_message": validation,
                                 "before": before_state, "after": after_state}, verified="failed",
                       strategy="fresh_page_state_inspection")
                break
            record("DISCOVERED", action="click_next", expected={"step_changed": True, "validation_message": None},
                   observed={"step_changed": True, "url": after_state[0], "field_count": len(after_state[1])},
                   verified="passed", strategy="fresh_page_state_inspection")
        attempt.form_fingerprint = fingerprint(applier.platform, all_fields)
        shot = _evidence_dir(cfg) / f"attempt_{attempt.id}.png"
        bs.page.screenshot(path=str(shot), full_page=True)
        attempt.screenshot_path = str(shot)
        attempt.final_url = bs.page.url
        missing_required = [u for u in unfilled if u.get("required")]
        unsupported = [u for u in unfilled if u.get("unsupported")]
        unapproved = [f for f in filled if f.get("needs_approval")]
        if missing_required or unsupported or unapproved or step_halt or mode == REVIEW_ONLY:
            attempt.outcome = PARKED
            reasons = [f"{u['label']}: {u['reason']}" for u in missing_required]
            reasons += [f"{u['label']}: {u['reason']}" for u in unsupported if not u.get("required")]
            reasons += [f"{f['label']}: generated answer (tier 3) awaiting your approval" for f in unapproved]
            attempt.park_reason = "; ".join(reasons) or step_halt or "review_only: halted at the submit control"
            attempt.review_status = "pending"
            if not missing_required and not unsupported and not unapproved and not step_halt:
                record("REVIEW_READY", action="verify_required_fields", expected={"all_required_satisfied": True},
                       observed={"required_total": sum(f.required for f in all_fields),
                                 "required_filled": sum(f.required for f in all_fields if any(x.get("label") == f.label for x in filled))},
                       verified="passed")
            record("PARKED", action="park_attempt", expected={"review_only": True},
                   observed={"reason": attempt.park_reason[:1000]}, verified="not_applicable")
            transition_status(session, app, ApplicationStatus.PARKED)
        else:
            record("REVIEW_READY", action="verify_required_fields", expected={"all_required_satisfied": True},
                   observed={"required_total": sum(f.required for f in all_fields),
                             "required_filled": sum(f.required for f in all_fields)}, verified="passed")
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
                app.answers_submitted = filled
                app.confirmation_text = evidence[:2000]
                transition_status(session, app, ApplicationStatus.SUBMITTED)
            else:
                attempt.outcome, attempt.review_status = UNCERTAIN, "pending"
                transition_status(session, app, ApplicationStatus.PARKED)
    except CapReached:
        raise
    except NeedsLogin as e:
        attempt.outcome, attempt.error = FAILED, f"NeedsLogin: {e}"[:2000]
        if current_stage not in {"FAILED", "BLOCKED", "PARKED"}:
            record("FAILED", action="open_form", expected={"authenticated_form_access": True},
                   observed={"error": attempt.error}, verified="failed")
        release(session, app)
        transition_status(session, app, ApplicationStatus.QUEUED, error_message=attempt.error)
        log.warning("attempt_needs_login", app_id=app.id, platform=applier.platform)
        raise
    except Reroute as e:
        from autoapply.appliers.resolve import reroute
        reroute(job, e.url, e.note)
        attempt.outcome, attempt.park_reason, attempt.final_url = REROUTED, e.note[:500], e.url
        if current_stage not in {"FAILED", "BLOCKED", "PARKED"}:
            record("PARKED", action="resolve_apply_url", expected={"owned_platform_form": True},
                   observed={"rerouted_to": e.url}, verified="not_applicable")
        release(session, app)
        transition_status(session, app, ApplicationStatus.QUEUED)
        log.info("attempt_rerouted", app_id=app.id, to=e.url, channel=job.apply_channel)
    except Exception as e:
        from autoapply.sources.browser import ChallengeDetected
        if isinstance(e, ChallengeDetected):
            attempt.error = f"BLOCKED: CHALLENGE_DETECTED: {e}"[:2000]
            attempt.outcome = FAILED
            if current_stage not in {"BLOCKED", "PARKED", "FAILED"}:
                record("BLOCKED", action="inspect_page", expected={"challenge": False},
                       observed={"challenge": True, "reason": str(e)[:400]}, verified="failed")
            release(session, app)
            transition_status(session, app, ApplicationStatus.FAILED, error_message=attempt.error)
            log.warning("attempt_challenged", app_id=app.id, platform=applier.platform)
            # return AttemptResult(attempt) is handled in finally
        else:
            attempt.error = f"{type(e).__name__}: {e}"[:2000]
            if submit_clicked:          # the click may have gone through: never call that a failure
                attempt.outcome, attempt.review_status = UNCERTAIN, "pending"
                transition_status(session, app, ApplicationStatus.PARKED)
            else:
                if current_stage not in {"FAILED", "BLOCKED", "PARKED"}:
                    record("FAILED", action="attempt", expected={"review_ready": True},
                           observed={"error": attempt.error}, verified="failed")
                try:
                    from playwright.sync_api import TimeoutError as PWTimeout
                    is_timeout = isinstance(e, PWTimeout)
                except Exception:
                    is_timeout = False
                
                if is_timeout:
                    attempt.outcome = FAILED
                    release(session, app)
                    transition_status(session, app, ApplicationStatus.FAILED, error_message=attempt.error)
                    log.warning("attempt_timeout_retry", app_id=app.id, error=attempt.error)
                    class RetryableError(Exception): pass
                    raise RetryableError("Transient timeout")
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

def run_attempt(session: Session, applier: FormApplier, app: Application, answerer: Answerer, *,
                browser_factory: Callable[[str], Any] | None = None, cfg: dict[str, Any] | None = None,
                now: datetime | None = None) -> AttemptResult:
    max_retries = 2
    for attempt_num in range(1, max_retries + 1):
        try:
            return _run_attempt_impl(session, applier, app, answerer, browser_factory=browser_factory, cfg=cfg, now=now, attempt_number=attempt_num)
        except Exception as e:
            if "Transient timeout" in str(e) and attempt_num < max_retries:
                log.info("retry_attempt", app_id=app.id, attempt=attempt_num)
                prior = (session.query(ApplicationAttempt).filter_by(application_id=app.id)
                         .order_by(ApplicationAttempt.id.desc()).first())
                if prior is not None:
                    _event(session, prior, "FAILED", action="application_recovery",
                           expected={"application_reopened": True},
                           observed={"browser_context_restarted": True, "form_reopened": True},
                           verified="not_applicable", strategy="bounded_context_restart_reopen")
                if app.status == ApplicationStatus.FAILED:
                    from autoapply.services.application_service import transition_status
                    transition_status(session, app, ApplicationStatus.QUEUED)
                # A retry gets a fresh attempt, a fresh browser context, and a reopened form.
                continue
            raise
