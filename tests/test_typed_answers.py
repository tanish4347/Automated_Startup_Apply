import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from autoapply.models.base import Base
from autoapply.models.vault import VaultAnswer, VaultIdentity, VaultPolicy
from autoapply.models.approved_answer import ApprovedAnswerCache
from autoapply.answers.engine import AnswerEngine, Resolution
from autoapply.appliers.harness import FormField
from autoapply.answers.normalization import normalize_answer, UserInputRequired, RejectAnswer

@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()

def test_normalization_date_object():
    """date/range object -> year/month/day"""
    val = {"start": "2020-01-01", "end": "2024-05-01"}
    
    # if expecting number (year)
    assert normalize_answer(val, "number") == "2024"
    
    # if expecting string, structured obj must fail
    with pytest.raises(RejectAnswer):
        normalize_answer(val, "string")

def test_normalization_boolean():
    """boolean concept -> radio/toggle"""
    # to boolean primitive
    assert normalize_answer("Yes", "boolean") == True
    assert normalize_answer("No", "boolean") == False
    
    # to option string
    assert normalize_answer("Yes", "boolean", ["yes", "no"]) == "yes"
    assert normalize_answer("True", "boolean", ["Yes", "No"]) == "Yes"

def test_normalization_string():
    """string -> select"""
    # Exact match
    assert normalize_answer("Software Engineer", "enum", ["Data Scientist", "Software Engineer"]) == "Software Engineer"
    
    # Semantic/Substring match
    assert normalize_answer("software", "enum", ["Data Scientist", "Software Engineer"]) == "Software Engineer"
    
    # Ambiguous match -> USER_INPUT_REQUIRED
    with pytest.raises(UserInputRequired):
        normalize_answer("software", "enum", ["Software Engineer", "Software Developer"])

def test_normalization_multi_select():
    assert set(normalize_answer("Java, Python", "multi-select", ["Java", "C++", "Python", "Go"])) == {"Java", "Python"}
    assert set(normalize_answer(["Java", "Python"], "multi-select", ["Java", "C++", "Python", "Go"])) == {"Java", "Python"}

def test_malformed_json():
    with pytest.raises(RejectAnswer):
        normalize_answer("some text", "number")

def test_semantic_question_matching(session):
    # Vault answers for identity.location
    v = VaultIdentity(id=1, first_name="Test")
    session.add(v)
    session.flush()
    session.add(VaultAnswer(canonical_key="identity.location", question="Current city", answer="Mumbai", status="CONFIRMED", source="USER ENTERED", identity_id=1))
    session.commit()
    
    eng = AnswerEngine(session)
    # The aliases are generated during tests or loaded. The index might need to be built.
    # Without fastembed it might fall back to exact/rule match or fail, but "identity.location" should be matched
    # if alias is added. Assuming aliases are added from QUESTIONS.md, it should match.
    # Let's seed a fake alias for testing.
    from autoapply.models.answer_alias import QuestionAlias
    session.add(QuestionAlias(alias="where are you currently located", canonical_key="identity.location", source="test", example=""))
    session.add(QuestionAlias(alias="current city", canonical_key="identity.location", source="test", example=""))
    session.commit()
    
    r1 = eng.resolve("Where are you currently located?", "text", [])
    assert r1.canonical_key == "identity.location"
    assert r1.value == "Mumbai"
    
    r2 = eng.resolve("Current city", "text", [])
    assert r2.canonical_key == "identity.location"
    assert r2.value == "Mumbai"

def test_approved_answer_cache_reuse(session):
    # Store in cache
    session.add(ApprovedAnswerCache(
        canonical_concept="custom.favorite_color",
        answer="Blue",
        confidence=1.0,
        source="user"
    ))
    session.commit()
    
    eng = AnswerEngine(session)
    # Wait, identify method reads from DB or Index. Let's add alias.
    from autoapply.models.answer_alias import QuestionAlias
    session.add(QuestionAlias(alias="favorite color", canonical_key="custom.favorite_color", source="test", example=""))
    session.commit()
    
    fld = FormField(label="Favorite color", field_type="text")
    r = eng.resolve(fld.label, fld.field_type, fld.options)
    assert r.canonical_key == "custom.favorite_color"
    assert r.value == "Blue"
    assert r.tier == "APPROVED_CACHE"

def test_ambiguous_option_matching(session):
    v = VaultIdentity(id=1, first_name="Test")
    session.add(v)
    session.add(VaultAnswer(canonical_key="custom.role", answer="Software", status="CONFIRMED", identity_id=1))
    from autoapply.models.answer_alias import QuestionAlias
    session.add(QuestionAlias(alias="role", canonical_key="custom.role", source="test", example=""))
    session.commit()
    
    eng = AnswerEngine(session)
    fld = FormField(label="role", field_type="select", options=["Software Engineer", "Software Developer"])
    r = eng.resolve(fld.label, fld.field_type, fld.options)
    assert r.value is None
    assert "ambiguous option match" in r.park_reason


def test_derived_facts_resolve_correctly(session):
    from autoapply.models.vault import VaultIdentity, VaultEmployment, VaultEducation
    
    # Insert identity
    ident = session.query(VaultIdentity).first()
    if not ident:
        ident = VaultIdentity(first_name="Test")
        session.add(ident)
        session.commit()
    
    session.add(VaultEmployment(identity_id=ident.id, company="Intern Corp", job_title="Software Intern", employment_type="Internship", start_date="2024", end_date="2025"))
    session.add(VaultEducation(identity_id=ident.id, institution="University", degree="Bachelor of Science", end_date="2026"))
    from autoapply.candidate.sensitive import user_input
    with user_input():
        session.commit()
    
    from autoapply.models.answer_alias import QuestionAlias
    session.add(QuestionAlias(alias="experience internship count", canonical_key="experience.internship_count", source="test"))
    session.add(QuestionAlias(alias="education current degree", canonical_key="education.current.degree", source="test"))
    session.commit()
    
    from autoapply.answers.engine import AnswerEngine
    eng = AnswerEngine(session)
    
    # Test internships
    r = eng.resolve("experience.internship_count", "text", [])
    assert r.value == "1"
    assert r.tier == "DERIVED"
    
    # Test degree
    r = eng.resolve("education.current.degree", "text", [])
    assert r.value == "Bachelor of Science"
    assert r.tier == "DERIVED"

def test_missing_sensitive_field_requests_user_input(session):
    from autoapply.answers.engine import AnswerEngine
    eng = AnswerEngine(session)
    
    from autoapply.candidate.sensitive import ParkApplication
    import pytest
    
    # gender is missing
    with pytest.raises(ParkApplication) as e:
        eng.resolve("gender", "select", ["Male", "Female", "Other"])
    
    assert "no confirmed answer" in str(e.value).lower()
