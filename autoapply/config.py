"""Application configuration via environment variables and YAML."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_project_root() -> Path:
    """Walk up from CWD to find the project root (where pyproject.toml lives)."""
    current = Path.cwd()
    for parent in [current, *current.parents]:
        if (parent / "pyproject.toml").exists():
            return parent
    return current


PROJECT_ROOT = _find_project_root()
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"


class DatabaseSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOAPPLY_DB_")

    url: str = Field(
        default=f"sqlite:///{DATA_DIR / 'autoapply.db'}",
        description="SQLAlchemy database URL",
    )
    echo: bool = Field(default=False, description="Echo SQL queries")


class SchedulerSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOAPPLY_SCHEDULER_")

    discovery_interval_minutes: int = Field(
        default=60, description="Minutes between discovery runs"
    )
    application_interval_minutes: int = Field(
        default=30, description="Minutes between application runs"
    )
    max_concurrent_applications: int = Field(
        default=3, description="Max concurrent application tasks"
    )


class BrowserSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOAPPLY_BROWSER_")

    headless: bool = Field(default=True, description="Run browser headless")
    timeout_ms: int = Field(default=30000, description="Default page timeout in ms")
    user_data_dir: str = Field(
        default=str(DATA_DIR / "browser_profile"),
        description="Persistent browser profile directory",
    )


class DashboardSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AUTOAPPLY_DASHBOARD_")

    host: str = Field(default="127.0.0.1", description="Dashboard bind host")
    port: int = Field(default=8080, description="Dashboard bind port")


class Settings(BaseSettings):
    """Root settings aggregating all sub-settings."""

    model_config = SettingsConfigDict(
        env_prefix="AUTOAPPLY_",
        env_nested_delimiter="__",
    )

    db: DatabaseSettings = Field(default_factory=DatabaseSettings)
    scheduler: SchedulerSettings = Field(default_factory=SchedulerSettings)
    browser: BrowserSettings = Field(default_factory=BrowserSettings)
    dashboard: DashboardSettings = Field(default_factory=DashboardSettings)

    log_level: str = Field(default="INFO", description="Root log level")
    log_file: str = Field(
        default=str(LOG_DIR / "autoapply.log"),
        description="Log file path",
    )
    config_file: str = Field(
        default=str(PROJECT_ROOT / "config.yaml"),
        description="Optional YAML config override file",
    )

    def apply_yaml_overrides(self) -> "Settings":
        """Load config.yaml (if it exists) and merge non-secret values."""
        cfg_path = Path(self.config_file)
        if cfg_path.exists():
            with open(cfg_path, "r") as f:
                overrides: dict[str, Any] = yaml.safe_load(f) or {}
            if overrides:
                return self.model_copy(update=_flatten_overrides(overrides))
        return self


def _flatten_overrides(d: dict, prefix: str = "") -> dict:
    """Flatten nested dict for pydantic model_copy update."""
    items: dict[str, Any] = {}
    for k, v in d.items():
        key = f"{prefix}{k}" if not prefix else f"{prefix}__{k}"
        if isinstance(v, dict):
            items.update(_flatten_overrides(v, key))
        else:
            items[k] = v
    return items


def get_settings() -> Settings:
    """Create and return the application settings."""
    settings = Settings()
    settings = settings.apply_yaml_overrides()
    # Ensure directories exist
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    return settings


# ── Discovery / search configuration (config/search.yaml) ──────────────────

class LinkedInSearch(BaseModel):
    locations: list[str] = ["India", "Remote"]
    max_pages: int = 2


class SourceToggles(BaseModel):
    remoteok: bool = True
    remotive: bool = True
    arbeitnow: bool = True
    linkedin: bool = True
    ats_boards: bool = True   # boards resolved by `autoapply ats resolve`
    unstop: bool = True
    internshala: bool = True
    instahyre: bool = True
    himalayas: bool = True
    adzuna: bool = True   # skipped (with a log line) unless ADZUNA_APP_ID/ADZUNA_APP_KEY are set
    # Browser tier: run only by `autoapply discover --browser`.
    naukri: bool = True
    wellfound: bool = True
    yc_waas: bool = True


class UnstopSearch(BaseModel):
    opportunities: list[str] = ["internships"]   # internships | jobs
    job_types: list[str] = []      # wfh | in_office | hybrid; empty = all
    search_terms: list[str] = []   # empty = every open listing (~700 internships)
    per_page: int = 100
    max_pages: int = 20


class InternshalaSearch(BaseModel):
    categories: list[str] = ["computer-science-internship"]  # URL slugs under /internships/
    max_pages: int = 20
    fetch_details: bool = True     # detail pages only for jobs that pass the filter; cached on disk


class InstahyreSearch(BaseModel):
    experience_levels: list[str] = ["internship"]   # internship | entry_level | associate | ...
    max_pages: int = 10            # 35 jobs per page


class HimalayasSearch(BaseModel):
    employment_types: list[str] = ["Intern"]   # /jobs/api/search?employment_type=
    max_pages: int = 80            # 20 jobs per page
    feed_pages: int = 0            # extra pages of the unfiltered cursor feed (94k jobs); 0 = off


class NaukriSearch(BaseModel):
    # Each search: keyword plus optional experience (years), wfhType (2 remote, 3 hybrid, 0 office),
    # cityTypeGid (134 = Mumbai).
    searches: list[dict[str, Any]] = [{"keyword": "internship"}]
    max_pages: int = 10            # 20 jobs per page
    fetch_details: bool = True     # opens the job page for jobs that pass the filter; cached


class WellfoundSearch(BaseModel):
    roles: list[str] = ["software-engineer"]
    locations: list[str] = ["remote"]   # "remote" or a Wellfound location slug (india, mumbai, ...)
    max_pages: int = 3


class YcWaasSearch(BaseModel):
    paths: list[str] = ["/jobs"]


class AdzunaSearch(BaseModel):
    queries: list[str] = ["intern"]
    results_per_page: int = 50
    max_pages: int = 5
    max_days_old: int = 30


class LocationPolicy(BaseModel):
    """Where the candidate can work. Used to compute Job.location_fit."""
    onsite_cities: list[str] = []
    remote: Literal["preferred", "allowed", "disallowed"] = "preferred"


class SearchConfig(BaseModel):
    queries: list[str]
    remoteok_tags: list[str] = []
    linkedin: LinkedInSearch = LinkedInSearch()
    sources: SourceToggles = SourceToggles()
    unstop: UnstopSearch = UnstopSearch()
    internshala: InternshalaSearch = InternshalaSearch()
    instahyre: InstahyreSearch = InstahyreSearch()
    himalayas: HimalayasSearch = HimalayasSearch()
    adzuna: AdzunaSearch = AdzunaSearch()
    naukri: NaukriSearch = NaukriSearch()
    wellfound: WellfoundSearch = WellfoundSearch()
    yc_waas: YcWaasSearch = YcWaasSearch()
    ats_boards_max_companies: int = 500
    location_policy: LocationPolicy = LocationPolicy()
    target_keywords: list[str]
    exclude_title_keywords: list[str] = []



SEARCH_CONFIG_PATH = PROJECT_ROOT / "config" / "search.yaml"


@lru_cache(maxsize=None)
def load_search_config(path: str | None = None) -> SearchConfig:
    """Load and validate config/search.yaml."""
    with open(path or SEARCH_CONFIG_PATH, "r", encoding="utf-8") as f:
        return SearchConfig.model_validate(yaml.safe_load(f) or {})
