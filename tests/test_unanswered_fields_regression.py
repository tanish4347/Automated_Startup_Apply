"""Regression tests for unanswered ATS fields audit and resolution fixes.

Covers:
1. CandidateKnowledgeBase naive/aware datetime handling and country/pronouns/graduation resolution.
2. Concept mapping for phrasing variations (course finish, degree course, pronouns, country, location).
3. Option matching for natural language affirmatives ('Yeah I would'), negatives, and consent options.
4. Ashby yes/no duplicate multi_select ['on'] filtering.
"""

import datetime
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from autoapply.models.base import Base
from autoapply.models.vault import VaultIdentity, VaultAnswer, VaultEducation
from autoapply.answers.knowledge import CandidateKnowledgeBase
from autoapply.candidate.concepts import map_question
from autoapply.answers.type_adapter import match_dropdown_option, NeedsUserInputError
from autoapply.appliers.ashby import AshbyApplier
from autoapply.appliers.harness import FormField


def test_knowledge_base_timezone_and_country_resolution():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    from autoapply.candidate.sensitive import user_input
    with user_input():
        # Identity with no country, city set
        ident = VaultIdentity(first_name="Tanish", last_name="Mutha", city="Kharagpur", pronouns="He/Him")
        session.add(ident)

        # Education with graduation year
        ed = VaultEducation(identity=ident, institution="IIT Kharagpur", degree="BS", major="Applied Geology", end_date="2028", status="CONFIRMED")
        session.add(ed)

        # Nationality in vault_answer
        nat = VaultAnswer(canonical_key="nationality", answer="Indian", status="CONFIRMED")
        session.add(nat)

        # VaultAnswer with naive valid_from and valid_until
        naive_past = datetime.datetime(2025, 1, 1, 0, 0, 0)
        naive_future = datetime.datetime(2030, 1, 1, 0, 0, 0)
        from autoapply.candidate.sensitive import USER
        test_ans = VaultAnswer(
            canonical_key="visa_sponsorship.uk",
            answer="Yeah I would",
            source=USER,
            status="CONFIRMED",
            valid_from=naive_past,
            valid_until=naive_future
        )
        session.add(test_ans)
        session.commit()

    kb = CandidateKnowledgeBase(session)

    # 1. Ensure naive datetime comparison does NOT raise TypeError
    sponsorship = kb.resolve("visa_sponsorship.uk")
    assert sponsorship is not None
    assert sponsorship.value == "Yeah I would"

    # 2. Country resolves to India from nationality fallback
    country_entry = kb.resolve("location.country")
    assert country_entry is not None
    assert country_entry.value == "India"

    # 3. Pronouns resolves to He/Him
    pronouns_entry = kb.resolve("pronouns")
    assert pronouns_entry is not None
    assert pronouns_entry.value == "He/Him"

    # 4. Graduation date resolves from education
    grad_entry = kb.resolve("graduation_date")
    assert grad_entry is not None
    assert grad_entry.value == "2028"


def test_concept_semantic_mapping():
    # Graduation / course completion phrasings
    assert map_question("What year does your course finish?") == "graduation_date"
    assert map_question("Course finishing year") == "graduation_date"

    # Degree course phrasings
    assert map_question("If you're studying a degree, let us know what degree course you are studying?") == "education.degree_title"

    # Preferred pronouns
    assert map_question("If you’d like to, please share your preferred pronouns (eg. they/them/theirs, she/her/hers).") == "pronouns"
    assert map_question("Preferred pronouns") == "pronouns"

    # Country
    assert map_question("Country") == "location.country"
    assert map_question("Your country") == "location.country"

    # Location (City)
    assert map_question("Location (City)") == "location.current.city"
    assert map_question("Location (Town)") == "location.current.city"


def test_dropdown_option_matching_enhancements():
    # 1. Natural language affirmative on Yes/No
    assert match_dropdown_option("Yeah I would", ["Yes", "No"]) == "Yes"
    assert match_dropdown_option("yeah", ["Yes", "No"]) == "Yes"
    assert match_dropdown_option("yep", ["Yes", "No"]) == "Yes"
    assert match_dropdown_option("sure", ["Yes", "No"]) == "Yes"

    # 2. Natural language negative on Yes/No
    assert match_dropdown_option("Nope", ["Yes", "No"]) == "No"
    assert match_dropdown_option("nah", ["Yes", "No"]) == "No"
    assert match_dropdown_option("no i do not", ["Yes", "No"]) == "No"

    # 3. Consent options with candidate 'Confirm' or 'I agree'
    text_consent_options = [
        "Yes - I consent to receiving text messages",
        "No - I do not consent to receiving text messages"
    ]
    assert match_dropdown_option("Confirm", text_consent_options) == "Yes - I consent to receiving text messages"
    assert match_dropdown_option("I agree", text_consent_options) == "Yes - I consent to receiving text messages"

    # 4. Ambiguous non-binary options should still raise NeedsUserInputError
    office_options = [
        "Yes and I am local to the London Area",
        "Yes but I would need to relocate",
        "No I am not willing to come into the office"
    ]
    try:
        match_dropdown_option("Yes", office_options)
        assert False, "Should raise NeedsUserInputError"
    except NeedsUserInputError:
        pass


def test_ashby_deduplicate_yesno_fields():
    applier = AshbyApplier()

    class FakePage:
        def evaluate(self, script):
            # Simulate YESNO_JS returning the yes/no questions
            return [
                {
                    "label": "Are you legally authorized to work in the UK?*",
                    "options": ["Yes", "No"],
                    "option_aa": ["yn0", "yn1"],
                    "required": True
                }
            ]

    class FakeBs:
        page = FakePage()

    # Create dummy base fields with the phantom duplicate (multi_select with ['on'])
    dummy_fields = [
        FormField("Legal Name", "text", True, None, "f0-0", "text"),
        FormField("Are you legally authorized to work in the UK?", "multi_select", False, ["on"], "f0-1", "checkbox"),
    ]

    class MockSuperAshbyApplier(AshbyApplier):
        def read_fields(self, bs):
            # Call actual ashby read_fields logic with mocked super()
            from autoapply.questions.harvest import clean_label
            yn_fields = []
            for g in bs.page.evaluate(""):
                lbl = clean_label(g["label"])
                if lbl:
                    yn_fields.append(FormField(lbl, "select", bool(g["required"]), g["options"],
                                             g["option_aa"][0], "yesno", g["option_aa"]))
            fields = list(dummy_fields)
            if yn_fields:
                yn_labels = {f.label.lower() for f in yn_fields}
                fields = [f for f in fields if not (f.label.lower() in yn_labels and f.field_type in ("multi_select", "checkbox") and f.options == ["on"])]
                fields.extend(yn_fields)
            return fields

    mock_applier = MockSuperAshbyApplier()
    resolved_fields = mock_applier.read_fields(FakeBs())

    # Ensure the duplicate multi_select ['on'] field was removed and only the select yesno field is present
    assert len(resolved_fields) == 2
    assert resolved_fields[0].label == "Legal Name"
    assert resolved_fields[1].label == "Are you legally authorized to work in the UK?"
    assert resolved_fields[1].field_type == "select"
    assert resolved_fields[1].options == ["Yes", "No"]
