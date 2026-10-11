"""Semantic candidate concepts stay distinct across identity, education, and compensation."""

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import autoapply.models  # noqa: F401
from autoapply.answers.engine import AnswerEngine
from autoapply.candidate.concepts import map_question
from autoapply.questions.harvest import normalize_label
from autoapply.candidate.intake import apply
from autoapply.candidate.sensitive import ParkApplication, SENSITIVE, USER, user_input
from autoapply.models.base import Base
from autoapply.models.vault import VaultAnswer, VaultIdentity, VaultPolicy
from autoapply.models.answer_alias import QuestionAlias


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.mark.parametrize(("label", "key"), [
    ("Salary per month", "compensation.monthly_salary"),
    ("Salary per year", "compensation.annual_salary"),
    ("Expected CTC", "compensation.expected_ctc"),
    ("Minimum stipend", "compensation.minimum_stipend"),
    ("Current city", "location.current.city"),
    ("Permanent city", "location.permanent.city"),
    ("Degree level", "education.degree_level"),
    ("Degree title", "education.degree_title"),
    ("Major", "education.major"),
    ("Specialization", "education.specialization"),
])
def test_semantic_mapper_keeps_explicit_concepts_distinct(label, key):
    assert map_question(label) == key


def test_bare_department_is_application_scoped_and_education_context_is_explicit():
    assert map_question("Department") == "application.department"
    assert map_question("Department", {"section": "education"}) == "education.department"
    assert map_question("Department", "education university profile") == "education.department"


def test_ambiguous_pay_and_city_labels_are_not_guessed():
    assert map_question("Salary") == "application.salary"
    assert map_question("City") == "application.location"
    assert map_question("Degree") == "application.degree"


def test_confirmation_persists_typed_provenance_and_sensitive_status(session):
    answer = VaultAnswer(canonical_key="compensation.monthly_salary", question="Monthly salary",
                        sensitivity=SENSITIVE, status="NEEDS REVIEW", source="FORM")
    session.add(answer)
    session.commit()

    apply(session, f"answer:{answer.id}", "confirm", {"value": "125000"})

    assert answer.answer == "125000"
    assert answer.value_json == {"amount": "125000", "currency": None, "period": "month", "raw": "125000"}
    assert answer.value_type == "money" and answer.scope == "candidate"
    assert answer.confirmed_by_user and answer.confirmed_at is not None
    assert answer.sensitivity == SENSITIVE and answer.source == USER


def test_monthly_compensation_does_not_answer_annual_compensation(session):
    answer = VaultAnswer(canonical_key="compensation.monthly_salary", question="Monthly salary",
                        sensitivity=SENSITIVE, status="NEEDS REVIEW", source="FORM")
    session.add(answer)
    session.commit()
    apply(session, f"answer:{answer.id}", "confirm", {"value": "125000"})

    with pytest.raises(ParkApplication):
        AnswerEngine(session, cfg={}, writeback=False).resolve("Annual salary", "number")


def test_compensation_renderer_refuses_an_explicit_currency_mismatch(session):
    answer = VaultAnswer(canonical_key="compensation.monthly_salary", question="Monthly salary",
                        sensitivity=SENSITIVE, status="NEEDS REVIEW", source="FORM")
    session.add(answer)
    session.commit()
    apply(session, f"answer:{answer.id}", "confirm", {"value": "125000"})
    answer.value_json = {"amount": "125000", "currency": "INR", "period": "month"}
    session.commit()

    result = AnswerEngine(session, cfg={}, writeback=False).resolve("Salary per month in USD", "number")
    assert result.value is None and result.park_reason == "stored value has incompatible type or unit"


def test_stipend_policy_is_scoped_to_minimum_stipend_without_legacy_mirror(session):
    with user_input():
        session.add(VaultPolicy(key="stipend_floor", value={"amount": 9000, "currency": "INR", "period": "month"},
                                source=USER, status="CONFIRMED"))
        session.commit()
    result = AnswerEngine(session, cfg={}, writeback=False).resolve("Minimum monthly stipend", "number")
    assert result.value == "9000"
    assert result.canonical_key == "compensation.minimum_stipend"
    assert session.query(VaultAnswer).filter_by(canonical_key="salary_expectation").count() == 0


def test_permanent_city_is_not_derived_from_current_city(session):
    session.add(VaultIdentity(city="Mumbai", country="India", status="CONFIRMED"))
    session.commit()
    engine = AnswerEngine(session, cfg={}, writeback=False)
    result = engine.resolve("Permanent city", "text")
    assert result.value is None and result.canonical_key == "location.permanent.city"


def test_200_harvested_question_concept_benchmark():
    """Frozen, unique labels sampled from five ATSs; expected concepts include 13 reviewed
    semantic corrections, with all other expected keys retaining the previous alias mapping."""
    fixture = Path(__file__).parent / "fixtures" / "candidate" / "concept_benchmark.json"
    cases = json.loads(fixture.read_text(encoding="utf-8"))
    assert len(cases) == 200
    assert {case["platform"] for case in cases} == {"ashby", "greenhouse", "lever", "smartrecruiters", "unstop"}

    engine_db = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine_db)
    benchmark_session = sessionmaker(bind=engine_db)()
    try:
        for case in cases:
            benchmark_session.add(QuestionAlias(
                alias=normalize_label(case["label"]), canonical_key=case["legacy_key"],
                source="harvest", example=case["label"],
            ))
        benchmark_session.commit()
        engine = AnswerEngine(benchmark_session, cfg={}, writeback=False)
        baseline = sum(case["legacy_key"] == case["expected_key"] for case in cases)
        after = sum(engine.identify(case["label"]).canonical_key == case["expected_key"] for case in cases)
        assert baseline == 187
        assert after == 200
        assert after > baseline
    finally:
        benchmark_session.close()
        engine_db.dispose()
