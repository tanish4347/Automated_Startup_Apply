import pytest
from autoapply.answers.type_adapter import adapt_value_to_type, match_dropdown_option, TypeValidationError

def test_numeric_type():
    assert adapt_value_to_type({"start": "2020-01-01"}, "number", "number") == "2020"
    assert adapt_value_to_type("1234", "number") == "1234"
    assert adapt_value_to_type(1234, "number") == "1234"
    assert adapt_value_to_type("I am 25 years old", "number") == "25"
    with pytest.raises(TypeValidationError):
         adapt_value_to_type("No numbers here", "number")

def test_boolean_type():
    assert adapt_value_to_type("Yes", "checkbox", "checkbox") is True
    assert adapt_value_to_type("No", "checkbox", "radio") is False

def test_dropdown_matching():
    options = ["United States", "United Kingdom", "Canada"]
    # Exact
    assert match_dropdown_option("Canada", options) == "Canada"
    # Normalized
    assert match_dropdown_option("canada", options) == "Canada"
    # Synonym
    assert match_dropdown_option("USA", options) == "United States"
    assert match_dropdown_option("US", options) == "United States"
    # Fuzzy
    assert match_dropdown_option("kingdom", options) == "United Kingdom"
    # No match
    assert match_dropdown_option("France", options) is None
