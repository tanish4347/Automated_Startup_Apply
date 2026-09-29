"""Base class for job source adapters."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterator


@dataclass
class SourceResult:
    """Normalized job data returned by a source adapter."""
    title: str
    company: str | None = None
    source: str = "unknown"
    source_id: str | None = None
    source_url: str | None = None
    application_url: str | None = None
    location: str | None = None
    work_mode: str = "unknown"
    description_raw: str | None = None
    description_text: str | None = None
    responsibilities: str | None = None
    requirements: str | None = None
    preferred_qualifications: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    compensation_text: str | None = None
    posted_date: datetime | None = None   # must be a datetime: the DB column rejects strings
    deadline: datetime | None = None
    ats_platform: str | None = None
    employment_type: str | None = None    # as the source states it, e.g. "Intern", "Full time"
    classification_category: str | None = None
    classification_evidence: str | None = None

    company_info: str | None = None
    posting_snapshot: str | None = None
    raw_data: dict[str, Any] | None = None
    tags: list[str] | None = None
    # The source says the posting no longer takes applications (e.g. Unstop regn_open=0).
    expired: bool = False
    # What the source knows about the employer, fed to get_or_create_company(). Keys (all
    # optional): domain, careers_url, about, is_india, india_cities.
    company_meta: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for ingestion."""
        from dataclasses import asdict
        return asdict(self)


class BaseSource(abc.ABC):
    """Abstract base class for job source adapters.

    Each concrete source adapter must implement:
    - name: property returning the source identifier
    - discover: method that yields SourceResult objects

    The orchestrator sets `budget` (autoapply.politeness.Budget) before a run; sources that make
    budgeted requests call budget.take() before each one. A source reports how its run went in
    `run_info` ({"status": "ok" | "degraded" | "failed", "error": ...}).
    """

    tier = "http"   # http | browser
    budget = None

    @property
    def run_info(self) -> dict[str, Any]:
        if "_run_info" not in self.__dict__:
            self._run_info = {"status": "ok"}
        return self._run_info

    def reset_run_info(self) -> None:
        self._run_info = {"status": "ok"}

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique identifier for this source."""
        ...

    @abc.abstractmethod
    def discover(self, **kwargs: Any) -> Iterator[SourceResult]:
        """Discover job postings from this source.

        Yields SourceResult objects for each discovered job.
        This should handle pagination, rate limiting, and errors internally.
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
