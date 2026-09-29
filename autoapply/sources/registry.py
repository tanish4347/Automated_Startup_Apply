"""Source adapter registry."""

from __future__ import annotations

from autoapply.logging import get_logger
from autoapply.sources.base import BaseSource

log = get_logger(__name__)

_registry: dict[str, BaseSource] = {}


def register_source(source: BaseSource) -> None:
    """Register a source adapter."""
    _registry[source.name] = source
    log.info("source_registered", name=source.name)


def get_source(name: str) -> BaseSource | None:
    """Get a registered source by name."""
    return _registry.get(name)


def list_sources() -> list[str]:
    """List all registered source names."""
    return list(_registry.keys())


def all_sources() -> list[BaseSource]:
    """Get all registered source adapters."""
    return list(_registry.values())
