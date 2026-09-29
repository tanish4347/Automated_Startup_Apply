"""Applier adapter registry."""

from __future__ import annotations

from autoapply.logging import get_logger
from autoapply.appliers.base import BaseApplier
from autoapply.models.job import Job

log = get_logger(__name__)

_registry: list[BaseApplier] = []


def register_applier(applier: BaseApplier) -> None:
    """Register an application adapter."""
    _registry.append(applier)
    log.info("applier_registered", name=applier.name)


def find_applier(job: Job) -> BaseApplier | None:
    """Find an applier that can handle the given job."""
    for applier in _registry:
        if applier.can_handle(job):
            return applier
    return None


def list_appliers() -> list[str]:
    """List all registered applier names."""
    return [a.name for a in _registry]
