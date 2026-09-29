import os
import pytest
from sqlalchemy.orm import Session
from autoapply.models.base import engine_from_settings, get_session_factory
from autoapply.config import get_settings
from autoapply.models.job import Job
from autoapply.models.application import Application, ApplicationStatus
from autoapply.models.candidate import CandidateProfile
from autoapply.appliers.greenhouse import GreenhouseApplier

def test_e2e_greenhouse_mock():
    settings = get_settings()
    engine = engine_from_settings(settings.db.url, False)
    SessionLocal = get_session_factory(engine)
    
    with SessionLocal() as session:
        profile = session.query(CandidateProfile).filter_by(is_active=1).first()
        mock_html_path = 'file:///' + os.path.abspath(os.path.join(os.path.dirname(__file__), 'mock_ats.html')).replace('\\\\', '/')
        
        job = Job(
            title="E2E Mock Job",
            company="MockInc",
            application_url=mock_html_path,
            ats_platform="greenhouse",
            source="manual",
            description_text="We are looking for someone to join us!"
        )
        session.add(job)
        session.commit()
        
        app = Application(job_id=job.id, status=ApplicationStatus.QUEUED)
        session.add(app)
        session.commit()
        
        applier = GreenhouseApplier()
        result = applier.apply(job, app, profile)
        
        print("Result Success:", result.success)
        print("Error:", result.error_message)
        print("Answers:", result.answers_submitted)

if __name__ == '__main__':
    test_e2e_greenhouse_mock()
