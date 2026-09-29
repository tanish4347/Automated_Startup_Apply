"""Normalize messy job-location strings into city / country / work mode."""

from __future__ import annotations

import re
from dataclasses import dataclass

# canonical city -> (country, aliases). Aliases are matched as whole words, longest first,
# so "Navi Mumbai" wins over "Mumbai" and "New Delhi" over "Delhi".
_CITIES: dict[str, tuple[str, list[str]]] = {
    "Bengaluru": ("India", ["bengaluru", "bangalore", "bengalore", "bangaluru", "blr"]),
    # Mumbai / Navi Mumbai / Thane include common localities: LinkedIn often gives only the suburb.
    "Mumbai": ("India", [
        "mumbai", "bombay", "andheri", "bandra", "bkc", "borivali", "kandivali", "malad", "goregaon",
        "jogeshwari", "vile parle", "santacruz", "juhu", "powai", "ghatkopar", "vikhroli", "kurla",
        "chembur", "mulund", "bhandup", "sion", "dadar", "worli", "lower parel", "parel", "colaba",
        "nariman point", "churchgate", "fort mumbai", "dahisar", "wadala", "marol", "saki naka",
    ]),
    "Navi Mumbai": ("India", [
        "navi mumbai", "new mumbai", "vashi", "nerul", "belapur", "cbd belapur", "kharghar", "airoli",
        "ghansoli", "sanpada", "seawoods", "turbhe", "panvel", "kopar khairane", "mahape", "rabale",
    ]),
    "Thane": ("India", ["thane", "mira road", "mira bhayandar", "bhayandar", "ghodbunder", "kalyan", "dombivli"]),
    "Delhi": ("India", ["delhi", "new delhi"]),
    "Delhi NCR": ("India", ["ncr", "delhi ncr", "delhi-ncr", "delhi/ncr", "national capital region"]),
    "Gurugram": ("India", ["gurugram", "gurgaon", "ggn"]),
    "Noida": ("India", ["noida", "greater noida"]),
    "Hyderabad": ("India", ["hyderabad", "secunderabad", "hyd"]),
    "Chennai": ("India", ["chennai", "madras"]),
    "Pune": ("India", ["pune", "poona"]),
    "Kolkata": ("India", ["kolkata", "calcutta"]),
    "Ahmedabad": ("India", ["ahmedabad", "amdavad"]),
    "Jaipur": ("India", ["jaipur"]),
    "Kochi": ("India", ["kochi", "cochin"]),
    "Chandigarh": ("India", ["chandigarh"]),
    "Mohali": ("India", ["mohali"]),
    "Indore": ("India", ["indore"]),
    "Coimbatore": ("India", ["coimbatore"]),
    "Thiruvananthapuram": ("India", ["thiruvananthapuram", "trivandrum"]),
    "Bhubaneswar": ("India", ["bhubaneswar"]),
    "Vadodara": ("India", ["vadodara", "baroda"]),
    "Nagpur": ("India", ["nagpur"]),
    "Mysuru": ("India", ["mysuru", "mysore"]),
    "Visakhapatnam": ("India", ["visakhapatnam", "vizag"]),
    "Lucknow": ("India", ["lucknow"]),
    "Surat": ("India", ["surat"]),
    "Goa": ("India", ["goa"]),
    "San Francisco": ("United States", ["san francisco", "sf bay area", "bay area"]),
    "New York": ("United States", ["new york", "nyc"]),
    "Seattle": ("United States", ["seattle"]),
    "London": ("United Kingdom", ["london"]),
    "Singapore": ("Singapore", ["singapore"]),
    "Berlin": ("Germany", ["berlin"]),
    "Toronto": ("Canada", ["toronto"]),
    "Dubai": ("United Arab Emirates", ["dubai"]),
}

# country -> whole-word aliases (lowercase). Indian states imply India.
_COUNTRIES: dict[str, list[str]] = {
    "India": ["india", "karnataka", "maharashtra", "telangana", "tamil nadu", "haryana",
              "uttar pradesh", "west bengal", "gujarat", "kerala", "rajasthan", "odisha",
              "punjab", "andhra pradesh", "madhya pradesh"],
    "United States": ["united states", "united states of america"],
    "United Kingdom": ["united kingdom", "england", "scotland"],
    "Canada": ["canada"],
    "Germany": ["germany", "deutschland"],
    "Singapore": ["singapore"],
    "United Arab Emirates": ["united arab emirates", "uae"],
}
# Case-sensitive codes that would be ambiguous in lowercase ("us", "in").
_COUNTRY_CODES = [
    (re.compile(r"\bUSA?\b|\bU\.S\.A?\.?"), "United States"),
    (re.compile(r"\bUK\b"), "United Kingdom"),
    (re.compile(r"(?:,|\()\s*IN\b"), "India"),
]

_REMOTE_RE = re.compile(
    r"\bremote\b|\bwork[\s-]from[\s-]home\b|\bwfh\b|\banywhere\b|\bdistributed\b|\bvirtual\b", re.I)
_HYBRID_RE = re.compile(r"\bhybrid\b", re.I)
_PLACEHOLDERS = {"unknown", "n/a", "na", "tbd", "-", "various", "multiple locations"}
_ONSITE_RE = re.compile(r"\bon[\s-]?site\b|\bin[\s-]office\b|\bwork from office\b|\bwfo\b", re.I)


def _alias_table(aliases: dict) -> list[tuple[re.Pattern, str]]:
    rows = [(alias, canon) for canon, (_, names) in aliases.items() for alias in names]
    rows.sort(key=lambda r: -len(r[0]))
    return [(re.compile(rf"(?<![a-z]){re.escape(a)}(?![a-z])"), c) for a, c in rows]


_CITY_PATTERNS = _alias_table(_CITIES)
_COUNTRY_PATTERNS = [(re.compile(rf"(?<![a-z]){re.escape(a)}(?![a-z])"), c)
                     for c, names in _COUNTRIES.items() for a in sorted(names, key=len, reverse=True)]


@dataclass(frozen=True)
class NormalizedLocation:
    city: str | None          # first city mentioned (canonical)
    country: str | None
    work_mode: str            # remote | hybrid | onsite | unknown
    cities: tuple[str, ...]   # every city mentioned, in order


def _city_matches(text: str) -> list[tuple[int, int, str]]:
    """(start, end, canonical city) for every alias in text, longest-first, no overlaps."""
    low = text.lower()
    hits: list[tuple[int, int, str]] = []
    for pattern, canon in _CITY_PATTERNS:
        for m in pattern.finditer(low):
            if not any(m.start() < e and s < m.end() for s, e, _ in hits):
                hits.append((m.start(), m.end(), canon))
    return sorted(hits)


def find_cities(text: str) -> list[str]:
    """Canonical cities mentioned in text, in order of appearance, deduplicated."""
    seen: list[str] = []
    for _, _, canon in _city_matches(text):
        if canon not in seen:
            seen.append(canon)
    return seen


def strip_cities(text: str) -> str:
    """Remove every city alias from text ("SDE Intern - Bangalore" -> "SDE Intern - ")."""
    for start, end, _ in reversed(_city_matches(text)):
        text = text[:start] + " " + text[end:]
    return text


def _find_country(text: str) -> str | None:
    low = text.lower()
    for pattern, canon in _COUNTRY_PATTERNS:
        if pattern.search(low):
            return canon
    for pattern, canon in _COUNTRY_CODES:
        if pattern.search(text):
            return canon
    return None


def normalize_location(raw: str | None, title: str | None = None) -> NormalizedLocation:
    """Parse a location string (plus optional job title for remote/hybrid hints)."""
    loc = (raw or "").strip()
    hint = f"{loc} {title or ''}"

    # The title only supplies a city when there is no location at all: a known city in the
    # title must not override an unrecognized town in the location field.
    cities = find_cities(loc) if loc else (find_cities(title) if title else [])
    country = _find_country(loc) or (_CITIES[cities[0]][0] if cities else None)

    if _HYBRID_RE.search(hint):
        mode = "hybrid"
    elif _REMOTE_RE.search(hint):
        mode = "remote"
    elif _ONSITE_RE.search(hint) or cities or (loc and loc.lower() not in _PLACEHOLDERS):
        mode = "onsite"
    else:
        mode = "unknown"

    return NormalizedLocation(
        city=cities[0] if cities else None, country=country, work_mode=mode, cities=tuple(cities),
    )
