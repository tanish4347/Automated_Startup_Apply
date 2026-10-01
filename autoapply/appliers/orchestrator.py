from autoapply.services.application_service import (
    get_queued_applications, 
    transition_status, 
    record_submission, 
    record_failure,
    reset_stalled_applications
)
from autoapply.candidate.manager import get_active_identity
from autoapply.models.application import ApplicationStatus

# Registry imports
from autoapply.appliers.registry import register_applier, find_applier
from autoapply.appliers.greenhouse import GreenhouseApplier
from autoapply.appliers.lever import LeverApplier
from autoapply.appliers.ashby import AshbyApplier
from autoapply.appliers.smartrecruiters import SmartRecruitersApplier

from autoapply.config import get_settings
from autoapply.models.base import engine_from_settings, get_session_factory
from autoapply.candidate.sensitive import ParkApplication
from autoapply.logging import get_logger
import time

log = get_logger(__name__)

# Harness appliers (autoapply/appliers/harness.py: review_only by default, evidence, outcomes).
HARNESS_APPLIERS: list = []


def setup_appliers() -> None:
    from autoapply.appliers.internshala import InternshalaApplier
    if not any(a.platform == "internshala" for a in HARNESS_APPLIERS):
        HARNESS_APPLIERS.append(InternshalaApplier())
    from autoapply.appliers.registry import list_appliers
    if "greenhouse" in list_appliers():
        return
    register_applier(GreenhouseApplier())
    register_applier(LeverApplier())
    register_applier(AshbyApplier())
    register_applier(SmartRecruitersApplier())


def _default_answerer():
    try:
        from autoapply.answers.engine import harness_answerer
        return harness_answerer()
    except ImportError:
        from autoapply.appliers.harness import Answer
        return lambda field, job: Answer(park_reason="no answer engine")

def run_application_engine(limit: int = 10) -> None:
    setup_appliers()
    
    settings = get_settings()
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    SessionLocal = get_session_factory(engine)
    
    processed = 0
    successes = 0
    
    with SessionLocal() as session:
        profile = get_active_identity(session)
        
        from autoapply.appliers.harness import CapReached, load_config, pause, run_attempt, submit_mode
        cfg = load_config()
        answerer = _default_answerer()
        stalled_count = reset_stalled_applications(session, timeout_minutes=float(cfg.get("stall_timeout_min", 30)))
        if stalled_count > 0:
            log.info('recovered_stalled_applications', count=stalled_count)
            
        from autoapply.services.application_service import queue_discovered
        queued_new = queue_discovered(session, limit=limit)
        if queued_new:
            log.info('queued_new_applications', count=len(queued_new))
            
        queued_apps = get_queued_applications(session, limit=limit)
        log.info('application_engine_started', queue_size=len(queued_apps))
        
        for app in queued_apps:
            log.info('processing_application', app_id=app.id, job_id=app.job_id)
            job = app.job
            harness = next((a for a in HARNESS_APPLIERS if a.can_handle(job)), None)
            if harness is not None:
                try:
                    run_attempt(session, harness, app, answerer, cfg=cfg)
                except CapReached as e:
                    log.info('daily_cap_reached', reason=str(e))
                    break
                processed += 1
                if processed < len(queued_apps):
                    pause(cfg, processed)
                continue
            applier = find_applier(job)
            # The pre-harness ATS appliers submit directly: they can't halt before submit, so
            # they run only where config/apply.yaml says submit_mode: live for their platform.
            if applier is not None and submit_mode(applier.name, cfg) != 'live':
                transition_status(session, app, ApplicationStatus.IN_PROGRESS)
                transition_status(session, app, ApplicationStatus.PARKED,
                                  error_message=f'{applier.name}: review_only, and this applier cannot halt '
                                                'before submit; not run')
                processed += 1
                continue
            transition_status(session, app, ApplicationStatus.IN_PROGRESS)
            
            if not applier:
                log.warning('no_applier_found', job_id=job.id, platform=job.ats_platform, source=job.source)
                record_failure(session, app, 'No suitable adapter found for this platform.')
                processed += 1
                continue
                
            log.info('applier_selected', applier=applier.name, job_id=job.id)
            try:
                result = applier.apply(job, app, profile)
                if result.success:
                    record_submission(
                        session, 
                        app, 
                        resume_used=result.resume_used,
                        answers_submitted=result.answers_submitted,
                        confirmation_text=result.confirmation_text
                    )
                    successes += 1
                    log.info('application_submitted', app_id=app.id)
                else:
                    record_failure(session, app, result.error_message or 'Unknown failure')
                    log.info('application_failed', app_id=app.id, error=result.error_message)
            except ParkApplication as e:
                log.info('application_parked', app_id=app.id, reason=str(e))
                app.error_message = str(e)
                transition_status(session, app, ApplicationStatus.PARKED)
                session.commit()
            except Exception as e:
                log.exception('application_exception', app_id=app.id, error=str(e))
                record_failure(session, app, f'Internal exception: {e}')
                
            processed += 1
            if processed < len(queued_apps):
                time.sleep(2)
                
    tiers = dict(getattr(answerer, 'state', {}).get('engine').stats) if getattr(answerer, 'state', {}).get('engine') else {}
    log.info('application_engine_finished', processed=processed, successes=successes, answer_tiers=tiers)
    if tiers:
        print('Fields per answer tier this run: ' + ', '.join(f'{k}={v}' for k, v in sorted(tiers.items())))
