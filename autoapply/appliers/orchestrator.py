"""The apply run: queue applications that have an applier, then run each through the harness.

Every applier is a harness FormApplier (registry.py): review_only unless config/apply.yaml says
exactly `live` for its platform. There is no legacy path. Applications whose job has no applier
are left where they are (never failed for lack of one); `autoapply appliers coverage` lists them.
A platform whose profile is not logged in (NeedsLogin) is skipped for the rest of the run.
"""

from __future__ import annotations

from autoapply.appliers.registry import find_applier, setup_appliers
from autoapply.config import get_settings
from autoapply.logging import get_logger
from autoapply.models.application import Application, ApplicationStatus
from autoapply.models.base import engine_from_settings, get_session_factory
from autoapply.services.application_service import reset_stalled_applications, transition_status

log = get_logger(__name__)


def _default_answerer():
    from autoapply.answers.engine import harness_answerer
    return harness_answerer()


def _with_applier(session, status: ApplicationStatus, limit: int, skip: set[str]) -> list[tuple[Application, object]]:
    order = Application.date_queued if status == ApplicationStatus.QUEUED else Application.date_discovered
    out = []
    for app in session.query(Application).filter(Application.status == status).order_by(order.asc()):
        applier = find_applier(app.job)
        if applier is not None and applier.platform not in skip:
            out.append((app, applier))
            if len(out) >= limit:
                break
    return out


def run_application_engine(limit: int = 10) -> None:
    """Run sync Playwright safely even when called by an async web handler.

    Playwright's sync API rejects execution in a thread with a running asyncio loop. The complete
    engine owns its SessionLocal and therefore runs as one unit on a worker thread in that case.
    """
    import asyncio
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        _run_application_engine(limit)
        return
    import threading
    errors: list[BaseException] = []

    def worker() -> None:
        try:
            _run_application_engine(limit)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=worker, name="application-engine", daemon=True)
    thread.start()
    thread.join()
    if errors:
        raise errors[0]


def _run_application_engine(limit: int = 10) -> None:
    from autoapply.appliers.harness import (
        CapReached, NeedsLogin, attempt_funnel_metrics, load_config, pause, recovery_metrics, run_attempt,
    )
    from autoapply.appliers.resolve import reroute_known
    setup_appliers()
    settings = get_settings()
    SessionLocal = get_session_factory(engine_from_settings(settings.db.url, settings.db.echo))
    cfg = load_config()
    answerer = _default_answerer()
    processed, skip = 0, set()
    with SessionLocal() as session:
        if reset_stalled_applications(session, timeout_minutes=float(cfg.get("stall_timeout_min", 30))):
            log.info("recovered_stalled_applications")
        rerouted = reroute_known(session)
        if rerouted:
            print(f"Re-tagged {rerouted} jobs whose stored apply link is off-platform (no request made).")
        for app, _ in _with_applier(session, ApplicationStatus.DISCOVERED, limit, skip):
            transition_status(session, app, ApplicationStatus.QUEUED)
        queue = _with_applier(session, ApplicationStatus.QUEUED, limit, skip)
        log.info("application_engine_started", queue_size=len(queue))
        for app, applier in queue:
            if applier.platform in skip:
                continue
            applier = find_applier(app.job) or applier     # a reroute earlier in the run may have re-tagged it
            try:
                run_attempt(session, applier, app, answerer, cfg=cfg)
            except CapReached as e:
                log.info("daily_cap_reached", reason=str(e))
                print(f"Stopped: {e}")
                break
            except NeedsLogin as e:
                skip.add(applier.platform)
                print(f"{applier.platform}: skipped for this run - {e}")
                continue
            except Exception as e:
                import traceback
                log.error("application_failed", app_id=app.id, error=str(e), trace=traceback.format_exc())
                app.status = ApplicationStatus.FAILED
                app.error_message = str(e)
                session.commit()
                continue
            processed += 1
            if processed < len(queue):
                pause(cfg, processed)
        metrics = recovery_metrics(session)
        log.info("application_recovery_metrics", **metrics)
        funnel = attempt_funnel_metrics(session)
        log.info("application_attempt_funnel", **funnel)
    eng = getattr(answerer, "state", {}).get("engine")
    tiers = dict(eng.stats) if eng else {}
    log.info("application_engine_finished", processed=processed, answer_tiers=tiers)
    print(f"Attempts this run: {processed}")
    if tiers:
        print("Fields per answer tier this run: " + ", ".join(f"{k}={v}" for k, v in sorted(tiers.items())))
    print("Persisted recovery metrics: " + ", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                                                       for k, v in sorted(metrics.items())))
    print("Persisted attempt funnel: " + ", ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}"
                                                    for k, v in sorted(funnel.items())))
