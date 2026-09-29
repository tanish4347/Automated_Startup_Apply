"""Applier adapter registry."""

from __future__ import annotations

from autoapply.logging import get_logger
from autoapply.appliers.base import BaseApplier
from autoapply.models.job import Job

log = get_logger(__name__)

_registry: list[BaseApplier] = []


def _is_linkedin(text: str | None) -> bool:
    return "linkedin" in (text or "").lower()


def register_applier(applier: BaseApplier) -> None:
    """Register an application adapter. LinkedIn appliers are forbidden, permanently: applying
    through LinkedIn means acting as the signed-in user, which this system never does."""
    if _is_linkedin(applier.name) or _is_linkedin(type(applier).__name__):
        raise AssertionError(f"LinkedIn appliers are forbidden: {applier.name!r}")
    _registry.append(applier)
    log.info("applier_registered", name=applier.name)


def find_applier(job: Job) -> BaseApplier | None:
    """Find an applier that can handle the given job. Never one for a LinkedIn-hosted job."""
    if _is_linkedin(job.application_url):
        return None
    for applier in _registry:
        if applier.can_handle(job):
            return applier
    return None


def list_appliers() -> list[str]:
    """List all registered applier names."""
    return [a.name for a in _registry]
