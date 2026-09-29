import pytest

from autoapply.sources.http_client import guess_work_mode, html_to_text


def test_html_to_text_emits_real_newlines():
    text = html_to_text("<p>Responsibilities</p><ul><li>Build models</li><li>Ship APIs</li></ul>")
    assert text == "Responsibilities\nBuild models\nShip APIs"
    assert "\\n" not in text


def test_html_to_text_empty():
    assert html_to_text("") == ""


@pytest.mark.parametrize("location, expected", [
    ("Remote - India", "remote"),
    ("Bengaluru (Hybrid)", "hybrid"),
    ("Mumbai, Maharashtra, India", "onsite"),
    ("", "unknown"),
    (None, "unknown"),
    ("Unknown", "unknown"),
])
def test_guess_work_mode(location, expected):
    assert guess_work_mode(location) == expected
