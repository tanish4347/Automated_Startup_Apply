from sqlalchemy.orm import Session

from autoapply.config import SearchConfig, get_settings, load_search_config
from autoapply.logging import get_logger
from autoapply.models.base import engine_from_settings, get_session_factory
from autoapply.services.job_service import ingest_job
from autoapply.sources.base import BaseSource
from autoapply.sources.filter import evaluate_job

from autoapply.sources.arbeitnow import ArbeitnowSource
from autoapply.sources.linkedin import LinkedInGuestSource
from autoapply.sources.remoteok import RemoteOkSource
from autoapply.sources.remotive import RemotiveSource
from autoapply.sources.career_pages import CareerPagesSource

log = get_logger(__name__)

def get_active_sources(cfg: SearchConfig) -> list[BaseSource]:
    on = cfg.sources
    sources: list[BaseSource] = []
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
    if on.career_pages:
        sources.append(CareerPagesSource(companies_file=cfg.career_pages_path))
    return sources

def run_discovery() -> None:
    settings = get_settings()
    engine = engine_from_settings(settings.db.url, settings.db.echo)
    SessionLocal = get_session_factory(engine)

    cfg = load_search_config()
    sources = get_active_sources(cfg)
    log.info('discovery_started', source_count=len(sources))

    with SessionLocal() as session:
        for source in sources:
            log.info('running_source', source=source.name)
            try:
                for result in source.discover():
                    try:
                        res_dict = result.to_dict() if hasattr(result, 'to_dict') else result.__dict__
                        
                        eval_res = evaluate_job(res_dict, cfg)
                        res_dict.update(eval_res)
                        
                        job = ingest_job(session, res_dict)
                        if job:
                            log.info('job_ingested', job_id=job.id, company=job.company, status=eval_res.get('classification_status', 'UNKNOWN'))
                    except Exception as e:
                        log.warning('job_ingest_failed', source=source.name, error=str(e))
                        session.rollback()
            except Exception as e:
                log.exception('source_failed', source=source.name, error=str(e))

    log.info('discovery_completed')




