"""The applier registry: every applier is a harness FormApplier (autoapply/appliers/harness.py).

Order matters: platform appliers (by apply_channel) first, then the classic ATSs (by ats_platform or
apply-URL host). LinkedIn appliers are forbidden, permanently: applying through LinkedIn means
acting as the signed-in user, which this system never does. register_applier refuses one, and
find_applier returns nothing for a LinkedIn-hosted job.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from autoapply.appliers.harness import FormApplier
from autoapply.logging import get_logger
from autoapply.models.job import Job

log = get_logger(__name__)

_registry: list[FormApplier] = []


def _is_linkedin(text: str | None) -> bool:
    return "linkedin" in (text or "").lower()


def register_applier(applier: FormApplier) -> None:
    if _is_linkedin(applier.platform) or _is_linkedin(type(applier).__name__):
        raise AssertionError(f"LinkedIn appliers are forbidden: {applier.platform!r}")
    if any(a.platform == applier.platform for a in _registry):
        return
    _registry.append(applier)
    log.debug("applier_registered", name=applier.platform)


def setup_appliers() -> list[FormApplier]:
    from autoapply.appliers.ashby import AshbyApplier
    from autoapply.appliers.greenhouse import GreenhouseApplier
    from autoapply.appliers.internshala import InternshalaApplier
    from autoapply.appliers.lever import LeverApplier
    from autoapply.appliers.naukri import NaukriApplier
    from autoapply.appliers.smartrecruiters import SmartRecruitersApplier
    from autoapply.appliers.unstop import UnstopApplier
    for cls in (InternshalaApplier, UnstopApplier, NaukriApplier, GreenhouseApplier, LeverApplier, AshbyApplier,
                SmartRecruitersApplier):
        register_applier(cls())
    return list(_registry)


def find_applier(job: Job) -> FormApplier | None:
    """The applier for this job, or None. Never one for a LinkedIn-hosted job."""
    if _is_linkedin(job.application_url) or _is_linkedin(job.resolved_apply_url):
        return None
    return next((a for a in _registry if a.can_handle(job)), None)


def list_appliers() -> list[str]:
    return [a.platform for a in _registry]


def coverage(session) -> dict[str, Any]:
    """For every in-policy active job: the applier that handles it. Counts per applier, the jobs
    with none grouped by apply_channel, and per source."""
    setup_appliers()
    jobs = session.query(Job).filter(Job.is_active == 1, Job.location_fit == "ok").all()
    by_applier: Counter = Counter()
    none_by_channel: Counter = Counter()
    by_source: dict[str, Counter] = {}
    rows = []
    for j in jobs:
        a = find_applier(j)
        name = a.platform if a else None
        by_applier[name or "(none)"] += 1
        by_source.setdefault(j.source, Counter())[name or "(none)"] += 1
        if a is None:
            none_by_channel[j.apply_channel or "unknown"] += 1
        rows.append((j.id, j.source, j.apply_channel, j.ats_platform, name))
    return {"total": len(jobs), "by_applier": dict(by_applier.most_common()), "none": by_applier.get("(none)", 0),
            "none_by_channel": dict(none_by_channel.most_common()),
            "by_source": {s: dict(c.most_common()) for s, c in sorted(by_source.items())}, "rows": rows}
