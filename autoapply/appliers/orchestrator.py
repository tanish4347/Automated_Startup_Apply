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
from autoapply.logging import get_logger
import time

log = get_logger(__name__)

def setup_appliers() -> None:
    register_applier(GreenhouseApplier())
    register_applier(LeverApplier())
    register_applier(AshbyApplier())
    register_applier(SmartRecruitersApplier())

def run_application_engine(limit: int = 10) -> None:
    setup_appliers()
    
    settings = get_settings()
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    SessionLocal = get_session_factory(engine)
    
    processed = 0
    successes = 0
    
    with SessionLocal() as session:
        profile = get_active_identity(session)
        
        stalled_count = reset_stalled_applications(session)
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
            transition_status(session, app, ApplicationStatus.IN_PROGRESS)
            job = app.job
            applier = find_applier(job)
            
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
            except Exception as e:
                log.exception('application_exception', app_id=app.id, error=str(e))
                record_failure(session, app, f'Internal exception: {e}')
                
            processed += 1
            if processed < len(queued_apps):
                time.sleep(2)
                
    log.info('application_engine_finished', processed=processed, successes=successes)
