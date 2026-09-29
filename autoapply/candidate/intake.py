"""Intake: the cards the /intake UI shows, in four passes, and applying the candidate's answers.

Passes, in order, so stopping halfway still leaves a usable Vault:
  a) cv      confirm what the CV parser found: catalog answers it filled, suggestions for
             SENSITIVE keys (grades, graduation date), and the education / internship / project /
             skill rows
  b) gap     catalog questions nothing has answered yet
  c) policy  structured rules (vault_policy) the reasoning tier reads: where on-site work is
             possible, remote, relocation, availability window, notice period, stipend floor
  d) story   the long free-text answers

Every write here runs inside sensitive.user_input(): this module is only called from the
candidate's own actions (the intake UI, the CLI).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from autoapply.candidate.sensitive import SENSITIVE, USER, user_input
from autoapply.models.vault import (
    VaultAnswer, VaultEducation, VaultEmployment, VaultIdentity, VaultPolicy, VaultProject, VaultSkill,
)

PASSES = ("cv", "gap", "policy", "story")
PASS_TITLES = {"cv": "Confirm what your CV says", "gap": "Fill the gaps", "policy": "Your rules",
               "story": "Story bank"}
SECONDS_PER_CARD = {"cv": 8, "gap": 20, "policy": 30, "story": 240}
CONFIRMED, SKIPPED, NEEDS_REVIEW = "CONFIRMED", "SKIPPED", "NEEDS REVIEW"

# answer key -> VaultIdentity column kept in sync when the answer is confirmed
IDENTITY_COLUMNS = {"first_name": "first_name", "last_name": "last_name", "email": "email", "phone": "phone",
                    "location_current": "city", "linkedin_url": "linkedin_url", "github_url": "github_url",
                    "portfolio_url": "portfolio_url", "preferred_name": "preferred_name"}

POLICY = [
    # key, question, input spec
    ("can_work_onsite_in", "Where can you work on-site or hybrid?",
     [{"name": "cities", "label": "Cities (comma-separated)", "type": "list"}]),
    ("remote_ok", "Will you work remotely?", [{"name": "value", "label": "Remote", "type": "bool"}]),
    ("will_relocate", "Will you relocate for an internship?", [{"name": "value", "label": "Relocate", "type": "bool"}]),
    ("availability_window", "When are you available?",
     [{"name": "start", "label": "Available from", "type": "date"},
      {"name": "end", "label": "Available until", "type": "date"}]),
    ("notice_period", "What is your notice period?", [{"name": "days", "label": "Days (0 = immediately)", "type": "number"}]),
    ("stipend_floor", "What is the lowest monthly stipend you will accept?",
     [{"name": "amount", "label": "Amount (INR per month, 0 = unpaid is fine)", "type": "number"}]),
]
POLICY_DEFAULTS = {"can_work_onsite_in": {"cities": ["Mumbai", "Navi Mumbai", "Thane"]},
                   "remote_ok": {"value": True}, "will_relocate": {"value": False}}


@dataclass
class Card:
    id: str
    intake_pass: str
    kind: str                     # answer | education | employment | project | skill | policy
    question: str
    inputs: list[dict[str, Any]]
    status: str                   # NEEDS REVIEW | CONFIRMED | SKIPPED
    sensitive: bool = False
    source: str | None = None
    note: str | None = None
    category: str | None = None


def _answer_inputs(a: VaultAnswer) -> list[dict[str, Any]]:
    kind = {"textarea": "textarea", "select": "text", "multi_select": "text", "boolean": "bool", "date": "text",
            "file": "text", "number": "number"}.get(a.field_type or "text", "text")
    if a.category == "story":
        kind = "textarea"
    # A SENSITIVE suggestion is shown, never pre-filled: the candidate types or accepts it.
    return [{"name": "value", "label": a.question, "type": kind, "value": a.answer or "",
             "suggestion": a.suggested_answer}]


def _answer_pass(a: VaultAnswer) -> str:
    if a.intake_pass == "cv" and (a.source == "CV" or a.suggested_answer):
        return "cv"
    return {"cv": "gap"}.get(a.intake_pass or "gap", a.intake_pass or "gap")


def build_cards(session: Session) -> list[Card]:
    cards: list[Card] = []
    answers = session.query(VaultAnswer).filter(VaultAnswer.canonical_key.isnot(None)).order_by(
        VaultAnswer.weight.desc().nullslast(), VaultAnswer.id).all()
    for a in answers:
        p = _answer_pass(a)
        if p == "policy":
            continue  # asked as structured rules in pass c
        note = None
        if a.suggested_answer:
            note = f"Your CV says: {a.suggested_answer}. Type it (or press Enter on an empty box to accept it)."
        elif a.source == "CV":
            note = "Read from your CV"
        cards.append(Card(f"answer:{a.id}", p, "answer", a.question or a.canonical_key, _answer_inputs(a),
                          a.status or NEEDS_REVIEW, a.sensitivity == SENSITIVE, a.source, note, a.category))
    for e in session.query(VaultEducation).filter(VaultEducation.source == "CV"):
        cards.append(Card(f"education:{e.id}", "cv", "education", f"Education: {e.institution}", [
            {"name": "institution", "label": "Institution", "type": "text", "value": e.institution or ""},
            {"name": "degree", "label": "Degree", "type": "text", "value": e.degree or ""},
            {"name": "major", "label": "Branch / major", "type": "text", "value": e.major or ""},
            {"name": "minor", "label": "Minor", "type": "text", "value": e.minor or ""},
            {"name": "start_date", "label": "Start", "type": "text", "value": e.start_date or ""},
            {"name": "end_date", "label": "Graduation (sensitive: you type it)", "type": "text", "value": e.end_date or ""},
            {"name": "cgpa", "label": "CGPA / score (sensitive: you type it)", "type": "text", "value": e.cgpa or ""},
            {"name": "scale", "label": "Out of (10, 4, %)", "type": "text", "value": e.scale or ""},
        ], e.status or NEEDS_REVIEW, source=e.source, category="education"))
    for r in session.query(VaultEmployment).filter(VaultEmployment.source == "CV"):
        cards.append(Card(f"employment:{r.id}", "cv", "employment", f"{r.job_title} at {r.company}", [
            {"name": "job_title", "label": "Title", "type": "text", "value": r.job_title or ""},
            {"name": "company", "label": "Company", "type": "text", "value": r.company or ""},
            {"name": "start_date", "label": "Start", "type": "text", "value": r.start_date or ""},
            {"name": "end_date", "label": "End", "type": "text", "value": r.end_date or ""},
            {"name": "description", "label": "What you did", "type": "textarea", "value": r.description or ""},
        ], r.status or NEEDS_REVIEW, source=r.source, category="experience"))
    for pr in session.query(VaultProject).filter(VaultProject.source == "CV"):
        cards.append(Card(f"project:{pr.id}", "cv", "project", f"Project: {pr.name}", [
            {"name": "name", "label": "Name", "type": "text", "value": pr.name or ""},
            {"name": "technologies", "label": "Stack", "type": "text", "value": pr.technologies or ""},
            {"name": "github_url", "label": "Repository", "type": "text", "value": pr.github_url or ""},
            {"name": "description", "label": "What it does", "type": "textarea", "value": pr.description or ""},
        ], pr.status or NEEDS_REVIEW, source=pr.source, category="projects"))
    for s in session.query(VaultSkill).filter(VaultSkill.source == "CV"):
        cards.append(Card(f"skill:{s.id}", "cv", "skill", f"Skills: {s.category}", [
            {"name": "name", "label": s.category, "type": "textarea", "value": s.name or ""},
        ], s.status or NEEDS_REVIEW, source=s.source, category="skills"))
    policies = {p.key: p for p in session.query(VaultPolicy)}
    for key, question, inputs in POLICY:
        row = policies.get(key)
        current = (row.value if row else None) or POLICY_DEFAULTS.get(key, {})
        filled = [{**i, "value": current.get(i["name"], "")} for i in inputs]
        cards.append(Card(f"policy:{key}", "policy", "policy", question, filled,
                          CONFIRMED if row else NEEDS_REVIEW, sensitive=key in ("notice_period", "stipend_floor",
                                                                                "availability_window"),
                          category="policy", note=None if row else "Pre-set from config/search.yaml: confirm or change"
                          if key in POLICY_DEFAULTS else None))
    order = {p: i for i, p in enumerate(PASSES)}
    return sorted(cards, key=lambda c: order[c.intake_pass])


def progress(cards: list[Card]) -> dict[str, Any]:
    passes = []
    for p in PASSES:
        mine = [c for c in cards if c.intake_pass == p]
        left = [c for c in mine if c.status != CONFIRMED]
        passes.append({"pass": p, "title": PASS_TITLES[p], "total": len(mine), "remaining": len(left),
                       "minutes_left": round(len(left) * SECONDS_PER_CARD[p] / 60, 1)})
    done = sum(1 for c in cards if c.status == CONFIRMED)
    resume_at = next((i for i, c in enumerate(cards) if c.status == NEEDS_REVIEW),
                     next((i for i, c in enumerate(cards) if c.status == SKIPPED), 0))
    return {"total": len(cards), "done": done, "remaining": len(cards) - done, "resume_at": resume_at, "passes": passes}


def state(session: Session) -> dict[str, Any]:
    cards = build_cards(session)
    return {"cards": [asdict(c) for c in cards], "progress": progress(cards)}


# ── applying the candidate's input ───────────────────────────────────────────

def _identity(session: Session) -> VaultIdentity:
    ident = session.query(VaultIdentity).first()
    if ident is None:
        ident = VaultIdentity(source=USER, status=CONFIRMED)
        session.add(ident)
        session.flush()
    return ident


def apply(session: Session, card_id: str, action: str, values: dict[str, Any] | None = None) -> None:
    """action: confirm | skip. values: {input name: value}. Runs as the candidate's own input."""
    values = values or {}
    kind, _, ref = card_id.partition(":")
    with user_input():
        if kind == "answer":
            _apply_answer(session, int(ref), action, values.get("value"))
        elif kind == "policy":
            _apply_policy(session, ref, action, values)
        else:
            _apply_fact(session, kind, int(ref), action, values)
        session.commit()


def _apply_answer(session: Session, answer_id: int, action: str, value: Any) -> None:
    a = session.get(VaultAnswer, answer_id)
    if action == "skip":
        if a.status != CONFIRMED:
            a.status = SKIPPED
        return
    value = (str(value).strip() if value is not None else "") or (a.suggested_answer if a.sensitivity == SENSITIVE else "") \
        or (a.answer or "")
    if not value:
        a.status = SKIPPED
        return
    if value != a.answer or a.sensitivity == SENSITIVE:
        a.source = USER
    a.answer, a.status = value, CONFIRMED
    if a.identity_id is None:
        a.identity = _identity(session)
    col = IDENTITY_COLUMNS.get(a.canonical_key or "")
    if col:
        setattr(_identity(session), col, value)


_FACT_MODELS = {"education": VaultEducation, "employment": VaultEmployment, "project": VaultProject, "skill": VaultSkill}


def _apply_fact(session: Session, kind: str, row_id: int, action: str, values: dict[str, Any]) -> None:
    row = session.get(_FACT_MODELS[kind], row_id)
    if action == "skip":
        if row.status != CONFIRMED:
            row.status = SKIPPED
        return
    edited = False
    for name, value in values.items():
        if hasattr(row, name) and name not in ("id", "identity_id", "source", "status"):
            value = value.strip() if isinstance(value, str) else value
            if (getattr(row, name) or "") != (value or ""):
                setattr(row, name, value or None)
                edited = True
    row.status = CONFIRMED
    if edited:
        row.source = USER


def _apply_policy(session: Session, key: str, action: str, values: dict[str, Any]) -> None:
    if action == "skip":
        return
    spec = {k: inputs for k, _, inputs in POLICY}[key]
    value: dict[str, Any] = {}
    for i in spec:
        v = values.get(i["name"])
        if i["type"] == "list":
            v = [x.strip() for x in (v if isinstance(v, list) else str(v or "").split(",")) if x.strip()]
        elif i["type"] == "bool":
            v = v in (True, "true", "yes", "on", "1", 1)
        elif i["type"] == "number":
            v = int(float(v)) if str(v or "").strip() not in ("",) else None
        else:
            v = str(v or "").strip() or None
        value[i["name"]] = v
    if key == "stipend_floor":
        value.update(currency="INR", period="month")
    row = session.query(VaultPolicy).filter_by(key=key).first()
    if row is None:
        row = VaultPolicy(key=key)
        session.add(row)
    row.value, row.source, row.status = value, USER, CONFIRMED
    _mirror_policy_answer(session, key, value)


# policy key -> catalog answer it also answers, verbatim from the structured value
_POLICY_ANSWERS = {"will_relocate": "relocation", "availability_window": "start_date",
                   "notice_period": "notice_period", "stipend_floor": "salary_expectation"}


def _mirror_policy_answer(session: Session, key: str, value: dict[str, Any]) -> None:
    target = _POLICY_ANSWERS.get(key)
    if not target:
        return
    a = session.query(VaultAnswer).filter_by(canonical_key=target).first()
    if a is None:
        return
    a.answer = json.dumps(value, sort_keys=True)
    a.source, a.status = USER, CONFIRMED
    if a.identity_id is None:
        a.identity = _identity(session)


# ── coverage (autoapply vault status) ───────────────────────────────────────

def coverage(session: Session) -> dict[str, Any]:
    by_cat: dict[str, list[int]] = {}
    blank_sensitive = []
    for a in session.query(VaultAnswer).filter(VaultAnswer.canonical_key.isnot(None)):
        slot = by_cat.setdefault(a.category or "other", [0, 0])
        slot[1] += 1
        answered = bool(a.answer) and a.status == CONFIRMED
        slot[0] += answered
        if a.sensitivity == SENSITIVE and not answered:
            blank_sensitive.append(a.canonical_key)
    policies = {p.key for p in session.query(VaultPolicy)}
    return {
        "answered": sum(v[0] for v in by_cat.values()), "total": sum(v[1] for v in by_cat.values()),
        "prefilled": session.query(VaultAnswer).filter((VaultAnswer.source == "CV") | VaultAnswer.suggested_answer.isnot(None)).count(),
        "by_category": dict(sorted(by_cat.items())),
        "blank_sensitive": sorted(blank_sensitive),
        "policy_set": sorted(policies), "policy_missing": [k for k, _, _ in POLICY if k not in policies],
    }
