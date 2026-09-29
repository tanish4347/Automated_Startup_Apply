import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401  (registers all tables)
from autoapply.candidate.manager import get_active_identity
from autoapply.models.base import Base
from autoapply.models.vault import VaultAnswer, VaultIdentity, VaultResume


def _session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_get_active_identity_requires_a_vault_row():
    with pytest.raises(RuntimeError):
        get_active_identity(_session())


def test_get_active_identity_requires_name_and_email():
    session = _session()
    session.add(VaultIdentity(first_name="Asha"))
    session.commit()
    with pytest.raises(ValueError):
        get_active_identity(session)


def test_profile_view_is_derived_from_vault_tables():
    session = _session()
    ident = VaultIdentity(first_name="Asha", last_name="Rao", email="a@x.in", city="Mumbai", country="India")
    ident.resumes = [
        VaultResume(file_name="old.pdf", file_path="data/resumes/old.pdf", is_active=False),
        VaultResume(file_name="new.pdf", file_path="data/resumes/new.pdf", is_active=True),
    ]
    ident.answers = [
        VaultAnswer(question="Notice period?", answer="Immediate", status="CONFIRMED"),
        VaultAnswer(question="CGPA?", answer="9.9", status="NEEDS REVIEW"),
    ]
    session.add(ident)
    session.commit()

    profile = get_active_identity(session)
    assert profile.full_name == "Asha Rao"
    assert profile.location == "Mumbai, India"
    assert profile.resume_path == "data/resumes/new.pdf"

    data = profile.to_profile_dict()
    assert data["identity"]["email"] == "a@x.in"
    # Unconfirmed answers must never reach the question engine.
    assert data["answers"] == [{"question": "Notice period?", "answer": "Immediate"}]
