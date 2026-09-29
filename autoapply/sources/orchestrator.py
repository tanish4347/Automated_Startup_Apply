"""Discovery runs. Two tiers, run by separate commands on separate cadences:

    autoapply discover             HTTP sources, in-process, one after another
    autoapply discover --browser   browser sources (Naukri, Wellfound, YC WaaS); each in its own
                                   child process with a hard timeout, and only when its cadence
                                   (config/politeness.yaml) says it is due

Every source run is logged in source_runs: counts, requests spent (for daily budgets) and status
(ok / degraded / failed / skipped).
"""

from __future__ import annotations

import multiprocessing
from collections import Counter
from datetime import datetime, timezone

from dotenv import load_dotenv
from sqlalchemy import func

from autoapply.config import PROJECT_ROOT, SearchConfig, get_settings, load_search_config
from autoapply.logging import get_logger
from autoapply.models.base import engine_from_settings, get_session_factory
from autoapply.models.job import Job
from autoapply.models.source_run import DEGRADED, FAILED, OK, RUNNING, SKIPPED, SourceRun
from autoapply.politeness import Budget, due, policy_for
from autoapply.services.job_service import ingest_job
from autoapply.sources.base import BaseSource
from autoapply.sources.filter import evaluate_job
from autoapply.sources.linkedin import LinkedInAuthViolation

log = get_logger(__name__)

BROWSER_SOURCES = ("naukri", "wellfound", "yc_waas")


def get_active_sources(cfg: SearchConfig, tier: str = "http") -> list[BaseSource]:
    on = cfg.sources
    sources: list[BaseSource] = []
    if tier == "browser":
        from autoapply.sources.naukri import NaukriSource
        from autoapply.sources.wellfound import WellfoundSource
        from autoapply.sources.yc_waas import YcWaasSource
        if on.naukri:
            sources.append(NaukriSource(cfg.naukri, search_cfg=cfg))
        if on.wellfound:
            sources.append(WellfoundSource(cfg.wellfound))
        if on.yc_waas:
            sources.append(YcWaasSource(cfg.yc_waas))
        return sources

    from autoapply.sources.adzuna import AdzunaSource
    from autoapply.sources.arbeitnow import ArbeitnowSource
    from autoapply.sources.ats_boards import AtsBoardsSource
    from autoapply.sources.himalayas import HimalayasSource
    from autoapply.sources.instahyre import InstahyreSource
    from autoapply.sources.internshala import InternshalaSource
    from autoapply.sources.linkedin import LinkedInGuestSource
    from autoapply.sources.remoteok import RemoteOkSource
    from autoapply.sources.remotive import RemotiveSource
    from autoapply.sources.unstop import UnstopSource
    if on.unstop:
        sources.append(UnstopSource(cfg.unstop))
    if on.internshala:
        sources.append(InternshalaSource(cfg.internshala, search_cfg=cfg))
    if on.instahyre:
        sources.append(InstahyreSource(cfg.instahyre))
    if on.himalayas:
        sources.append(HimalayasSource(cfg.himalayas))
    if on.adzuna:
        sources.append(AdzunaSource(cfg.adzuna))
    if on.remoteok:
        sources.append(RemoteOkSource(tags=cfg.remoteok_tags))
    if on.remotive:
        sources.append(RemotiveSource(searches=cfg.queries))
    if on.arbeitnow:
        sources.append(ArbeitnowSource())
    if on.linkedin:
        sources.append(LinkedInGuestSource(
            keywords=cfg.queries, locations=cfg.linkedin.locations, max_pages=cfg.linkedin.max_pages,
        ))
    if on.ats_boards:
        sources.append(AtsBoardsSource(max_companies=cfg.ats_boards_max_companies))
    return sources


def run_source(session, source: BaseSource, cfg: SearchConfig) -> Counter:
    """Run one source, ingest what it yields, and log the run in source_runs."""
    counts: Counter = Counter()
    run = SourceRun(source=source.name, tier=source.tier, status=RUNNING)
    session.add(run)
    session.commit()
    run_id = run.id
    source.reset_run_info()
    source.budget = Budget.for_source(session, source.name, policy_for(source.name))
    max_id_before = session.query(func.max(Job.id)).scalar() or 0
    created: set[int] = set()
    violation: LinkedInAuthViolation | None = None
    log.info('running_source', source=source.name, tier=source.tier, budget_left=source.budget.remaining)
    try:
        for result in source.discover():
            counts["fetched"] += 1
            try:
                res_dict = result.to_dict()
                eval_res = evaluate_job(res_dict, cfg)
                res_dict.update(eval_res)
                job = ingest_job(session, res_dict)
                if job is None:
                    continue
                if job.id > max_id_before and job.id not in created:
                    created.add(job.id)
                    counts["new"] += 1
                    counts["accepted"] += eval_res["classification_status"] != "AUTO_REJECT"
                else:
                    counts["merged"] += 1
            except Exception as e:
                counts["failed"] += 1
                log.warning('job_ingest_failed', source=source.name, error=str(e))
                session.rollback()
    except LinkedInAuthViolation as e:
        violation = e
        source.run_info.update(status=FAILED, error=f"LinkedInAuthViolation: {e}")
    except Exception as e:
        log.exception('source_failed', source=source.name, error=str(e))
        source.run_info.update(status=FAILED, error=f"{type(e).__name__}: {e}")

    run = session.get(SourceRun, run_id)
    run.status = source.run_info.get("status", OK)
    run.error = source.run_info.get("error")
    run.requests = source.budget.used
    run.finished_at = datetime.now(timezone.utc)
    for k in ("fetched", "new", "merged", "accepted", "failed"):
        setattr(run, k, counts[k])
    session.commit()
    log.info('source_done', source=source.name, status=run.status, requests=run.requests, **counts)
    if violation is not None:
        raise violation  # never swallowed: stop the whole run
    return counts


def _session_factory():
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    settings = get_settings()
    return get_session_factory(engine_from_settings(settings.db.url, settings.db.echo))


def run_discovery(only: list[str] | None = None, tier: str = "http",
                  headless: bool = True) -> dict[str, Counter]:
    """Run every enabled source of a tier in this process (or just `only`). Returns per-source
    counts: fetched, new (jobs created), merged (matched a stored job), accepted (new jobs
    classified AUTO_ACCEPT/REVIEW), failed (ingest errors)."""
    SessionLocal = _session_factory()
    cfg = load_search_config()
    sources = [s for s in get_active_sources(cfg, tier) if not only or s.name in only]
    for s in sources:
        if s.tier == "browser":
            s.headless = headless
    log.info('discovery_started', tier=tier, source_count=len(sources))
    stats: dict[str, Counter] = {}
    with SessionLocal() as session:
        for source in sources:
            stats[source.name] = run_source(session, source, cfg)
    log.info('discovery_completed', **{name: dict(c) for name, c in stats.items()})
    return stats


# ── Browser tier: one child process per source, hard timeout, cadence ───────

def _browser_worker(name: str, headless: bool) -> None:
    from autoapply.logging import setup_logging
    settings = get_settings()
    setup_logging(settings.log_level, settings.log_file)
    run_discovery(only=[name], tier="browser", headless=headless)


def run_browser_discovery(only: list[str] | None = None, force: bool = False,
                          headless: bool = True) -> dict[str, str]:
    """Run each due browser source in a child process. Returns {source: outcome}."""
    SessionLocal = _session_factory()
    cfg = load_search_config()
    names = [s.name for s in get_active_sources(cfg, "browser") if not only or s.name in only]
    outcomes: dict[str, str] = {}
    ctx = multiprocessing.get_context("spawn")
    for name in names:
        policy = policy_for(name)
        with SessionLocal() as session:
            if not force and not due(session, name, policy):
                session.add(SourceRun(source=name, tier="browser", status=SKIPPED,
                                      finished_at=datetime.now(timezone.utc),
                                      error=f"not due (cadence {policy.cadence_hours}h)"))
                session.commit()
                outcomes[name] = "skipped: not due"
                log.info("browser_source_not_due", source=name, cadence_hours=policy.cadence_hours)
                continue
        proc = ctx.Process(target=_browser_worker, args=(name, headless), name=f"browser-{name}")
        proc.start()
        proc.join(timeout=policy.run_timeout_min * 60)
        if proc.is_alive():
            proc.kill()
            proc.join(10)
            _mark_timed_out(SessionLocal, name, policy.run_timeout_min)
            outcomes[name] = f"killed after {policy.run_timeout_min} min"
            continue
        with SessionLocal() as session:
            last = (session.query(SourceRun).filter(SourceRun.source == name)
                    .order_by(SourceRun.id.desc()).first())
            outcomes[name] = f"{last.status} (new={last.new}, requests={last.requests})" if last else f"exit {proc.exitcode}"
    return outcomes


def _mark_timed_out(SessionLocal, name: str, minutes: float) -> None:
    with SessionLocal() as session:
        run = (session.query(SourceRun).filter(SourceRun.source == name, SourceRun.status == RUNNING)
               .order_by(SourceRun.id.desc()).first())
        if run is not None:
            run.status, run.error = FAILED, f"timeout: killed after {minutes} min"
            run.finished_at = datetime.now(timezone.utc)
            session.commit()
    log.error("browser_source_timeout", source=name, minutes=minutes)
