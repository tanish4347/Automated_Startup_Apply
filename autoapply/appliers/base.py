"""Base class for application automation adapters."""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from autoapply.models.job import Job
from autoapply.models.application import Application
from autoapply.models.vault import VaultIdentity


@dataclass
class ApplyResult:
    """Result of an application attempt."""
    success: bool
    confirmation_id: str | None = None
    confirmation_text: str | None = None
    error_message: str | None = None
    error_details: dict[str, Any] | None = None
    resume_used: str | None = None
    answers_submitted: dict[str, Any] | None = None
    posting_snapshot: str | None = None


class BaseApplier(abc.ABC):
    """Abstract base class for ATS/platform application adapters.

    Each concrete applier must implement:
    - name: property returning the ATS/platform identifier
    - can_handle: method to check if this applier can handle a given job
    - apply: method that attempts to submit an application
    """

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """Unique identifier for this applier."""
        ...

    @abc.abstractmethod
    def can_handle(self, job: Job) -> bool:
        """Check if this applier can handle the given job."""
        ...

    @abc.abstractmethod
    def apply(self, job: Job, application: Application, profile: VaultIdentity) -> ApplyResult:
        """Attempt to apply to the job.

        Returns an ApplyResult with the outcome.
        """
        ...

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} name={self.name!r}>"
