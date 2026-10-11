"""Canonical candidate concepts and conservative semantic mapping for form questions.

The registry describes meaning; it does not manufacture candidate values. In particular,
application-scoped questions stay scoped to that application and ambiguous pay/location labels
are not silently treated as interchangeable facts.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Concept:
    key: str
    category: str
    value_type: str = "text"
    sensitivity: str = "NORMAL"
    scope: str = "candidate"
    period: str | None = None
    aliases: tuple[str, ...] = ()


CONCEPTS: dict[str, Concept] = {
    "location.current.city": Concept("location.current.city", "location", aliases=("current city", "city where you live", "present city")),
    "location.permanent.city": Concept("location.permanent.city", "location", aliases=("permanent city", "permanent residence city", "home town city")),
    "education.university": Concept("education.university", "education", aliases=("university", "college", "university name", "college name")),
    "education.department": Concept("education.department", "education", aliases=("academic department", "department of study", "department at university")),
    "education.degree_level": Concept("education.degree_level", "education", aliases=("degree level", "level of degree")),
    "education.degree_title": Concept("education.degree_title", "education", aliases=("degree title", "degree name", "title of degree")),
    "education.major": Concept("education.major", "education", aliases=("major", "field of study", "primary field of study")),
    "education.minor": Concept("education.minor", "education", aliases=("minor", "secondary field of study")),
    "education.specialization": Concept("education.specialization", "education", aliases=("specialization", "specialisation", "area of specialization")),
    "compensation.monthly_salary": Concept("compensation.monthly_salary", "compensation", "money", "SENSITIVE", period="month", aliases=("monthly salary", "salary per month", "monthly pay")),
    "compensation.annual_salary": Concept("compensation.annual_salary", "compensation", "money", "SENSITIVE", period="year", aliases=("annual salary", "yearly salary", "salary per year", "annual pay")),
    "compensation.stipend": Concept("compensation.stipend", "compensation", "money", "SENSITIVE", period="month", aliases=("stipend", "monthly stipend", "stipend amount")),
    "compensation.expected_ctc": Concept("compensation.expected_ctc", "compensation", "money", "SENSITIVE", period="year", aliases=("expected ctc", "expected annual ctc", "expected cost to company")),
    "compensation.current_ctc": Concept("compensation.current_ctc", "compensation", "money", "SENSITIVE", period="year", aliases=("current ctc", "current cost to company")),
    "compensation.minimum_stipend": Concept("compensation.minimum_stipend", "compensation", "money", "SENSITIVE", period="month", aliases=("minimum stipend", "minimum monthly stipend", "stipend floor", "lowest stipend accepted")),
    # Employer-specific department questions describe an application response, not a candidate fact.
    "application.department": Concept("application.department", "application", scope="application", aliases=("department",)),
    "application.location": Concept("application.location", "application", scope="application"),
    "application.salary": Concept("application.salary", "application", scope="application"),
    "application.degree": Concept("application.degree", "application", scope="application"),
    "application.education": Concept("application.education", "application", scope="application"),
    "application.education_status": Concept("application.education_status", "application", scope="application"),
}


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("application.education_status", re.compile(r"\b(enrolled|currently a .* student|return to (?:the )?program)\b", re.I)),
    ("compensation.minimum_stipend", re.compile(r"\b(minimum|minimum acceptable|lowest|floor)\b.*\bstipend\b|\bstipend\b.*\b(minimum|lowest|floor)\b", re.I)),
    ("compensation.expected_ctc", re.compile(r"\b(expected|desired|target)\b.*\b(ctc|cost to company)\b|\b(ctc|cost to company)\b.*\b(expected|desired|target)\b", re.I)),
    ("compensation.current_ctc", re.compile(r"\b(current|present)\b.*\b(ctc|cost to company)\b|\b(ctc|cost to company)\b.*\b(current|present)\b", re.I)),
    ("compensation.monthly_salary", re.compile(r"\b(monthly salary|salary per month|monthly pay)\b", re.I)),
    ("compensation.annual_salary", re.compile(r"\b(annual salary|yearly salary|salary per year|annual pay)\b", re.I)),
    ("compensation.stipend", re.compile(r"\b(stipend|internship allowance)\b", re.I)),
    ("location.permanent.city", re.compile(r"\b(permanent|home town|hometown)\b.*\b(city|location|address)\b|\b(city|location|address)\b.*\b(permanent|home town|hometown)\b", re.I)),
    ("location.current.city", re.compile(r"\b(current|present|currently living)\b.*\b(city|location|address)\b|\b(city|location|address)\b.*\b(current|present)\b", re.I)),
    ("education.degree_level", re.compile(r"\b(degree level|level of degree)\b", re.I)),
    ("education.degree_level", re.compile(r"\b(degree type|highest level of education|level of education completed)\b", re.I)),
    ("education.degree_title", re.compile(r"\b(degree title|degree name|title of degree|what degree (?:are you|do you)\s+(?:currently )?pursuing)\b", re.I)),
    ("graduation_date", re.compile(r"\bwhat year did you graduate (?:from )?(?:high )?school\b", re.I)),
    ("education.specialization", re.compile(r"\b(speciali[sz]ation|area of focus)\b", re.I)),
    ("education.major", re.compile(r"\b(major|field of study)\b", re.I)),
    ("education.department", re.compile(r"\b(academic department|department of study|department at (?:the )?(?:university|college))\b", re.I)),
    ("education.minor", re.compile(r"\bminor\b", re.I)),
    ("compensation.annual_salary", re.compile(r"\b(expected|desired)\s+(?:salary|compensation)\s*\(?\s*(?:yearly|annual)\b", re.I)),
    ("education.university", re.compile(r"\b(university|college|institution)\b", re.I)),
)


def map_question(label: str, context: dict[str, Any] | str | None = None) -> str | None:
    """Return a canonical key only when the wording gives a reliable semantic distinction."""
    text = " ".join((label or "").lower().split())
    context_text = ""
    if isinstance(context, str):
        context_text = context.lower()
    elif context:
        context_text = " ".join(str(v) for v in context.values() if v is not None).lower()
    combined = f"{text} {context_text}"
    for key, pattern in _RULES:
        if pattern.search(text):
            return key
    if re.fullmatch(r"department(?: name)?", text):
        if re.search(r"education|university|college|school|academic|degree", context_text):
            return "education.department"
        return "application.department"
    if text in {"city", "location", "your location"}:
        return "application.location"
    if text in {"salary", "compensation"}:
        return "application.salary"
    if text == "degree":
        return "application.degree"
    if text == "education":
        return "application.education"
    # Exact registry aliases are useful for short but unambiguous labels; avoid guessing on bare
    # "salary", "city", "degree", or "specialty".
    for concept in CONCEPTS.values():
        if text in {a.lower() for a in concept.aliases}:
            return concept.key
    if "education" in combined and text in {"degree", "program degree"}:
        return "education.degree_title"
    if "education" in combined and text == "department":
        return "education.department"
    return None


def concept_for(key: str | None) -> Concept | None:
    return CONCEPTS.get(key or "")


def metadata_for(key: str | None) -> dict[str, Any]:
    concept = concept_for(key)
    if concept is None:
        return {"value_type": "text", "scope": "candidate", "conditions": {}}
    return {
        "value_type": concept.value_type,
        "scope": concept.scope,
        "conditions": {"period": concept.period} if concept.period else {},
    }


def render_value(value: Any, key: str, field_type: str = "text", label: str = "") -> Any:
    """Render a typed fact only when its stored unit matches the requested concept."""
    concept = concept_for(key)
    if not isinstance(value, dict) or "amount" not in value:
        return value
    if concept is None or concept.value_type != "money":
        return None
    if value.get("period") != concept.period:
        return None
    currency = (value.get("currency") or "").upper()
    symbols = {"$": {"USD", "CAD", "AUD"}, "€": {"EUR"}, "£": {"GBP"}, "₹": {"INR"}}
    asked = re.search(r"\b(INR|USD|GBP|EUR|CAD|AUD|SGD|JPY)\b|[$€£₹]", label or "", re.I)
    if asked:
        token = asked.group(0).upper()
        accepted = symbols.get(token, {token})
        if currency not in accepted:
            return None
    amount = str(value["amount"])
    # Free text preserves the currency; numeric controls receive only an amount after any
    # explicit currency in the employer's label has been checked.
    return amount if field_type == "number" else f"{amount} {currency}".strip()
