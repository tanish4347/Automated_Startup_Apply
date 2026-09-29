import os
import json
from sqlalchemy.orm import Session
from autoapply.models.candidate import CandidateProfile
from autoapply.logging import get_logger

log = get_logger(__name__)

PROFILE_PATH = r'c:\Users\Tanish Mutha\Downloads\Automated apllications\candidate_profile.json'

def get_or_create_default_profile(session: Session) -> CandidateProfile:
    \"\"\"Get the active candidate profile from JSON configuration.\"\"\"
    if not os.path.exists(PROFILE_PATH):
        raise RuntimeError(f"Candidate profile missing. Please create {PROFILE_PATH}")
        
    with open(PROFILE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    if not data.get('full_name') or not data.get('email'):
        raise ValueError(f"Candidate profile at {PROFILE_PATH} is incomplete. Please fill out full_name and email.")
        
    profile = session.query(CandidateProfile).filter_by(is_active=1).first()
    
    if not profile:
        log.info("creating_db_profile_from_json")
        profile = CandidateProfile(is_active=1)
        session.add(profile)
        
    # Sync JSON to DB
    profile.full_name = data.get('full_name')
    profile.email = data.get('email')
    profile.phone = data.get('phone')
    profile.location = data.get('location')
    profile.linkedin_url = data.get('linkedin_url')
    profile.github_url = data.get('github_url')
    profile.portfolio_url = data.get('portfolio_url')
    profile.resume_path = data.get('resume_path')
    profile.resume_text = data.get('resume_text')
    profile.education = data.get('education', [])
    profile.experience = data.get('experience', [])
    profile.skills = data.get('skills', [])
    # Also save projects if we add a column later, or just store in a dynamic json column if available
    
    session.commit()
    session.refresh(profile)
    
    # Attach raw data for dynamic question answering
    profile.raw_json = data
        
    return profile
