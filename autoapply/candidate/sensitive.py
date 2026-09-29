"""SENSITIVE answers: attestations the candidate signs. User-entered only.

The keys below (work authorisation, visa sponsorship, criminal history, disability / veteran /
demographic self-identification, CGPA and graduation dates, salary or stipend expectations, notice
period and start date) are:
- written only from the candidate's own input: every write happens inside `user_input()`, which
  only the intake UI and CLI enter. A database-level guard (before_flush) rejects any other write,
  including the CV parser, the answer engine and any LLM path;
- retrieved verbatim by the answer engine (`sensitive_answer`), never inferred, reworded or
  generated;
- if missing or unconfirmed at apply time, the applier parks the form (`ParkApplication`).

The same guard covers VaultEducation.cgpa / scale / end_date (grades and graduation dates) and
every VaultPolicy row (the structured rules the reasoning tier reads).
"""

from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from sqlalchemy import event, inspect
from sqlalchemy.orm import Session

SENSITIVE = "SENSITIVE"
USER = "USER ENTERED"

SENSITIVE_KEYS = {
    "visa_sponsorship", "work_authorization", "criminal_history",
    "gender", "race_ethnicity", "veteran_status", "disability_status", "pronouns",
    "gpa", "graduation_date", "salary_expectation", "notice_period", "start_date",
}
# custom.* clusters about criminal history / background checks are sensitive too
_SENSITIVE_CUSTOM = re.compile(r"criminal|convict|felony|misdemeanou?r|background_check|arrest")

EDUCATION_SENSITIVE_COLUMNS = ("cgpa", "scale", "end_date")

_user_input: ContextVar[bool] = ContextVar("vault_user_input", default=False)


class SensitiveWriteError(PermissionError):
    """Something other than the candidate tried to write a SENSITIVE field."""


class ParkApplication(Exception):
    """A SENSITIVE answer is missing at apply time: the applier parks the form for the candidate."""


def is_sensitive_key(canonical_key: str | None) -> bool:
    key = canonical_key or ""
    base = key.split(".")[0]           # work_authorization.us -> work_authorization
    return base in SENSITIVE_KEYS or (key.startswith("custom.") and bool(_SENSITIVE_CUSTOM.search(key)))


@contextmanager
def user_input() -> Iterator[None]:
    """Mark writes as the candidate's own input. Entered only by the intake UI / CLI handlers."""
    token = _user_input.set(True)
    try:
        yield
    finally:
        _user_input.reset(token)


def _changed(obj, attr: str) -> bool:
    hist = inspect(obj).attrs[attr].history
    return bool(hist.added) and hist.added != hist.deleted


@event.listens_for(Session, "before_flush")
def _guard_sensitive_writes(session: Session, flush_context, instances) -> None:
    if _user_input.get():
        return
    from autoapply.models.vault import VaultAnswer, VaultEducation, VaultPolicy
    for obj in list(session.new) + list(session.dirty):
        if isinstance(obj, VaultAnswer):
            sensitive = obj.sensitivity == SENSITIVE or is_sensitive_key(obj.canonical_key)
            if sensitive and _changed(obj, "answer") and obj.answer not in (None, ""):
                raise SensitiveWriteError(f"{obj.canonical_key}: SENSITIVE answers are written only by the candidate")
            if _changed(obj, "sensitivity") and inspect(obj).attrs["sensitivity"].history.deleted == [SENSITIVE]:
                raise SensitiveWriteError(f"{obj.canonical_key}: sensitivity can't be lowered")
            if sensitive and obj.status == "CONFIRMED" and _changed(obj, "status"):
                raise SensitiveWriteError(f"{obj.canonical_key}: only the candidate confirms a SENSITIVE answer")
        elif isinstance(obj, VaultEducation):
            for col in EDUCATION_SENSITIVE_COLUMNS:
                if _changed(obj, col) and getattr(obj, col) not in (None, ""):
                    raise SensitiveWriteError(f"vault_education.{col} is written only by the candidate")
        elif isinstance(obj, VaultPolicy):
            if obj in session.new or any(_changed(obj, a) for a in ("value", "status")):
                raise SensitiveWriteError(f"vault_policy.{obj.key} is written only by the candidate")


def sensitive_answer(session: Session, canonical_key: str) -> str:
    """The candidate's own answer, verbatim. Raises ParkApplication if it's blank or unconfirmed,
    trying the country-specific key first (work_authorization.us) then the generic one."""
    from autoapply.models.vault import VaultAnswer
    keys = [canonical_key]
    if "." in canonical_key and not canonical_key.startswith("custom."):
        keys.append(canonical_key.split(".")[0])
    for key in keys:
        row = session.query(VaultAnswer).filter_by(canonical_key=key).first()
        if row is not None and row.answer and row.status == "CONFIRMED" and row.source == USER:
            return row.answer
    raise ParkApplication(f"{canonical_key}: no confirmed answer from the candidate; parking this application")
