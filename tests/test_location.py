import pytest

from autoapply.sources.location import find_cities, normalize_location, strip_cities


@pytest.mark.parametrize("raw, city, country, mode", [
    # Bengaluru spellings
    ("Bengaluru, Karnataka, India", "Bengaluru", "India", "onsite"),
    ("Bangalore", "Bengaluru", "India", "onsite"),
    ("BLR", "Bengaluru", "India", "onsite"),
    ("Bangalore Urban, Karnataka", "Bengaluru", "India", "onsite"),
    ("bengaluru (hybrid)", "Bengaluru", "India", "hybrid"),
    ("Hybrid - Bangalore, IN", "Bengaluru", "India", "hybrid"),
    # Mumbai region
    ("Mumbai, Maharashtra", "Mumbai", "India", "onsite"),
    ("Mumbai, Maharashtra, India", "Mumbai", "India", "onsite"),
    ("Bombay", "Mumbai", "India", "onsite"),
    ("Navi Mumbai", "Navi Mumbai", "India", "onsite"),
    ("Thane West, Maharashtra", "Thane", "India", "onsite"),
    ("Borivali, Maharashtra, India", "Mumbai", "India", "onsite"),
    ("Andheri East, Mumbai", "Mumbai", "India", "onsite"),
    ("Powai", "Mumbai", "India", "onsite"),
    ("Vashi, Navi Mumbai", "Navi Mumbai", "India", "onsite"),
    ("Airoli", "Navi Mumbai", "India", "onsite"),
    ("Mira Road East", "Thane", "India", "onsite"),
    ("Mumbai (On-site)", "Mumbai", "India", "onsite"),
    # NCR
    ("Gurugram, Haryana", "Gurugram", "India", "onsite"),
    ("Gurgaon", "Gurugram", "India", "onsite"),
    ("Delhi NCR", "Delhi NCR", "India", "onsite"),
    ("NCR", "Delhi NCR", "India", "onsite"),
    ("New Delhi, Delhi, India", "Delhi", "India", "onsite"),
    ("Noida, Uttar Pradesh", "Noida", "India", "onsite"),
    ("Greater Noida", "Noida", "India", "onsite"),
    # Other Indian cities
    ("Hyderabad, Telangana", "Hyderabad", "India", "onsite"),
    ("Secunderabad", "Hyderabad", "India", "onsite"),
    ("Chennai, Tamil Nadu, India", "Chennai", "India", "onsite"),
    ("Pune", "Pune", "India", "onsite"),
    ("Kolkata, West Bengal", "Kolkata", "India", "onsite"),
    ("Calcutta", "Kolkata", "India", "onsite"),
    ("Kochi, Kerala", "Kochi", "India", "onsite"),
    ("Trivandrum", "Thiruvananthapuram", "India", "onsite"),
    ("Ahmedabad, Gujarat", "Ahmedabad", "India", "onsite"),
    # Remote / WFH
    ("Remote", None, None, "remote"),
    ("Remote (India)", None, "India", "remote"),
    ("Remote - India", None, "India", "remote"),
    ("India (Remote)", None, "India", "remote"),
    ("WFH", None, None, "remote"),
    ("Work From Home", None, None, "remote"),
    ("work-from-home", None, None, "remote"),
    ("Anywhere in the world", None, None, "remote"),
    ("Bengaluru (Remote)", "Bengaluru", "India", "remote"),
    # Country only / multi-city
    ("India", None, "India", "onsite"),
    ("Bengaluru / Mumbai", "Bengaluru", "India", "onsite"),
    ("Mumbai, Pune, Bangalore", "Mumbai", "India", "onsite"),
    # International
    ("San Francisco, CA", "San Francisco", "United States", "onsite"),
    ("New York, NY, USA", "New York", "United States", "onsite"),
    ("Remote, US", None, "United States", "remote"),
    ("London, UK", "London", "United Kingdom", "onsite"),
    ("Singapore", "Singapore", "Singapore", "onsite"),
    ("Berlin, Germany", "Berlin", "Germany", "onsite"),
    # Empty / placeholder
    ("", None, None, "unknown"),
    (None, None, None, "unknown"),
    ("Unknown", None, None, "unknown"),
    ("Multiple Locations", None, None, "unknown"),
])
def test_normalize_location(raw, city, country, mode):
    loc = normalize_location(raw)
    assert (loc.city, loc.country, loc.work_mode) == (city, country, mode)


def test_all_cities_are_kept_in_order():
    assert normalize_location("Mumbai, Pune, Bangalore").cities == ("Mumbai", "Pune", "Bengaluru")


def test_longest_alias_wins_so_navi_mumbai_is_not_also_mumbai():
    assert find_cities("Navi Mumbai") == ["Navi Mumbai"]


def test_city_names_inside_other_words_do_not_match():
    assert find_cities("Punekar Industries, Goanna Street") == []


def test_title_supplies_city_and_mode_when_location_is_empty():
    loc = normalize_location("", "ML Intern – Mumbai (On-site)")
    assert (loc.city, loc.work_mode) == ("Mumbai", "onsite")
    assert normalize_location("", "Backend Intern (Remote)").work_mode == "remote"


def test_title_city_does_not_override_an_unrecognized_location():
    loc = normalize_location("Uran, Maharashtra, India", "AI Trainer Internship in Navi Mumbai")
    assert (loc.city, loc.country) == (None, "India")


def test_strip_cities_removes_aliases():
    assert strip_cities("SDE Intern - Bangalore").strip(" -") == "SDE Intern"
