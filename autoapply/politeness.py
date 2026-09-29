"""Pacing and daily request budgets from config/politeness.yaml.

A Budget counts requests: those already spent today (summed from source_runs) plus those taken
in this run. Every request a budgeted source makes goes through Budget.take(), which raises
BudgetExhausted at the cap. Pacer sleeps a random interval before each request, with an extra
long pause every N requests, so traffic never comes in bursts.
"""

from __future__ import annotations

import random
import time
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import Callable

import yaml
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from autoapply.config import PROJECT_ROOT
from autoapply.logging import get_logger

log = get_logger(__name__)

POLITENESS_PATH = PROJECT_ROOT / "config" / "politeness.yaml"


class BudgetExhausted(Exception):
    """The source hit its daily request cap. Not an error: the run stops cleanly."""


class SourcePolicy(BaseModel):
    daily_requests: int = 2000
    min_delay_s: float = 1.0
    max_delay_s: float = 3.0
    long_pause_every: int = 0
    long_pause_s: tuple[float, float] = (20.0, 60.0)
    cadence_hours: float = 0
    run_timeout_min: float = 30


@lru_cache(maxsize=None)
def _load(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def policy_for(source: str, path: Path | None = None) -> SourcePolicy:
    raw = _load(str(path or POLITENESS_PATH))
    return SourcePolicy.model_validate({**(raw.get("defaults") or {}), **((raw.get("sources") or {}).get(source) or {})})


def utc_day_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def requests_spent_today(session: Session, source: str, now: datetime | None = None) -> int:
    from autoapply.models.source_run import SourceRun
    total = (session.query(func.coalesce(func.sum(SourceRun.requests), 0))
             .filter(SourceRun.source == source, SourceRun.started_at >= utc_day_start(now)).scalar())
    return int(total or 0)


def last_run_started(session: Session, source: str) -> datetime | None:
    from autoapply.models.source_run import SKIPPED, SourceRun
    row = (session.query(SourceRun.started_at).filter(SourceRun.source == source, SourceRun.status != SKIPPED)
           .order_by(SourceRun.started_at.desc()).first())
    if row is None:
        return None
    started = row[0]
    return started if started.tzinfo else started.replace(tzinfo=timezone.utc)


def due(session: Session, source: str, policy: SourcePolicy, now: datetime | None = None) -> bool:
    """False while the source's last run is younger than its cadence."""
    last = last_run_started(session, source)
    now = now or datetime.now(timezone.utc)
    return last is None or now - last >= timedelta(hours=policy.cadence_hours)


class Budget:
    def __init__(self, source: str, limit: int, spent_before: int = 0):
        self.source, self.limit, self.spent_before, self.used = source, limit, spent_before, 0

    @classmethod
    def for_source(cls, session: Session, source: str, policy: SourcePolicy | None = None) -> "Budget":
        policy = policy or policy_for(source)
        return cls(source, policy.daily_requests, requests_spent_today(session, source))

    @property
    def remaining(self) -> int:
        return max(self.limit - self.spent_before - self.used, 0)

    def take(self) -> None:
        if self.remaining <= 0:
            raise BudgetExhausted(f"{self.source}: daily budget of {self.limit} requests used up")
        self.used += 1


class Pacer:
    def __init__(self, policy: SourcePolicy, sleep: Callable[[float], None] = time.sleep,
                 rng: random.Random | None = None):
        self.policy, self._sleep, self._rng, self.count = policy, sleep, rng or random.Random(), 0

    def wait(self) -> float:
        """Sleep before the next request. The first request of a run goes out without a pause."""
        p = self.policy
        delay = 0.0 if self.count == 0 else self._rng.uniform(p.min_delay_s, p.max_delay_s)
        if self.count and p.long_pause_every and self.count % p.long_pause_every == 0:
            delay += self._rng.uniform(*p.long_pause_s)
        self.count += 1
        if delay:
            self._sleep(delay)
        return delay
