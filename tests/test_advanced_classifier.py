import pytest
from src.acquisition.filter import AdvancedJobClassifier

def test_classifier_cases():
    c = AdvancedJobClassifier()

    # 1. Remote ML internship -> KEEP
    r1 = c.evaluate("ML Intern", "Python, PyTorch", work_mode="REMOTE")
    assert r1["is_target_role"] is True
    assert r1["role_type"] == "REMOTE_INTERNSHIP"

    # 2. Bangalore onsite ML internship -> KEEP
    r2 = c.evaluate("ML Intern", "Python", location="Bangalore")
    assert r2["is_target_role"] is True
    assert r2["role_type"] == "NON_REMOTE_INTERNSHIP"

    # 3. Mumbai onsite ML internship -> REJECT
    r3 = c.evaluate("ML Intern", "Python", location="Mumbai")
    assert r3["is_target_role"] is False
    assert r3["rejection_category"] == "Mumbai non-remote"

    # 4. Remote AI research internship -> KEEP
    r4 = c.evaluate("AI Research Internship", "LLMs", location="Remote")
    assert r4["is_target_role"] is True

    # 5. Remote Software Engineer — New Grad -> KEEP
    r5 = c.evaluate("Software Engineer — New Grad", "Java", work_mode="REMOTE")
    assert r5["is_target_role"] is True
    assert r5["role_type"] == "REMOTE_ENTRY_LEVEL_FULL_TIME"

    # 6. Remote Data Scientist — Entry Level -> KEEP
    r6 = c.evaluate("Remote Data Scientist", "Entry level", work_mode="REMOTE")
    assert r6["is_target_role"] is True

    # 7. Remote ML Engineer — 0–2 years -> KEEP
    r7 = c.evaluate("ML Engineer", "0-2 years experience", work_mode="REMOTE")
    assert r7["is_target_role"] is True

    # 8. Remote Senior ML Engineer -> REJECT
    r8 = c.evaluate("Senior ML Engineer", "5+ years", work_mode="REMOTE")
    assert r8["is_target_role"] is False
    assert r8["rejection_category"] == "Senior/experienced"

    # 10. Bangalore Full-Time Software Engineer — Entry Level -> REJECT
    r10 = c.evaluate("Software Engineer - Entry Level", "Bangalore full time")
    assert r10["is_target_role"] is False
    assert r10["rejection_category"] == "Non-internship/non-remote full-time"

    # 12. Marketing Intern -> REJECT
    r12 = c.evaluate("Marketing Intern", "SEO")
    assert r12["is_target_role"] is False
    assert r12["rejection_category"] == "Wrong field"

    # 16. Research Assistant — NLP -> KEEP
    r16 = c.evaluate("Research Assistant — NLP", "Transformer models")
    assert r16["is_target_role"] is True
