import re
from typing import Dict, Any

class AdvancedJobClassifier:
    """
    Classifies jobs according to strict target rules:
    1. Remote/Hybrid/Onsite Internship -> KEEP (unless Onsite + Mumbai -> REJECT)
    2. Remote Entry-Level Full-Time -> KEEP
    3. Onsite/Hybrid Full-Time -> REJECT
    4. Senior Full-Time -> REJECT
    """

    TARGET_FIELDS = [
        "data science", "machine learning", "ai", "genai", "data analytics",
        "nlp", "computer vision", "research", "data engineering",
        "ml engineering", "software engineering", "backend"
    ]

    EXCLUDED_LOCATIONS = ["mumbai"]

    INTERNSHIP_TERMS = [
        "internship", "intern", "summer intern", "winter intern",
        "research intern", "student researcher", "research assistant",
        "co-op", "student co-op", "trainee", "graduate trainee",
        "industrial trainee"
    ]

    SENIOR_TERMS = [
        r"\bsenior\b", r"\blead\b", r"\bstaff\b", r"\bprincipal\b",
        r"\bmanager\b", r"\bdirector\b", r"\bhead\b", r"\barchitect\b",
        r"5\+\s*years", r"7\+\s*years"
    ]

    ENTRY_LEVEL_TERMS = [
        "entry level", "entry-level", "junior", "graduate", "new grad",
        "associate", "0-1 years", "0-2 years", "1-2 years"
    ]

    def _determine_field(self, text: str) -> str:
        for field in self.TARGET_FIELDS:
            if field in text.lower():
                return field.title()
        return "Other Technical"

    def _determine_work_mode(self, text: str, explicit_location: str) -> str:
        text_lower = text.lower()
        loc_lower = (explicit_location or "").lower()

        if "remote" in text_lower or "remote" in loc_lower or "work from home" in text_lower:
            return "REMOTE"
        if "hybrid" in text_lower or "hybrid" in loc_lower:
            return "HYBRID"
        if "onsite" in text_lower or "on-site" in text_lower or "in office" in text_lower or loc_lower:
            # If there's a specific city mentioned and no remote word, assume onsite/hybrid
            return "ONSITE"

        return "UNKNOWN"

    def _is_senior(self, text: str) -> bool:
        for term in self.SENIOR_TERMS:
            if re.search(term, text.lower()):
                return True
        return False

    def _is_internship(self, text: str) -> bool:
        return any(term in text.lower() for term in self.INTERNSHIP_TERMS)

    def _is_entry_level(self, text: str) -> bool:
        return any(term in text.lower() for term in self.ENTRY_LEVEL_TERMS)

    def _calculate_relevance(self, text: str) -> float:
        # Basic mock relevance based on keyword density of tech skills
        skills = ["python", "c++", "sql", "pytorch", "llms", "pandas", "fastapi"]
        score = 0.5 # Base
        matches = sum(1 for s in skills if s in text.lower())
        score += matches * 0.05
        return min(0.99, score)

    def evaluate(self, title: str, description: str, location: str = "", work_mode: str = "") -> Dict[str, Any]:
        full_text = f"{title} {description}".lower()

        # 1. Base extractions
        target_field = self._determine_field(full_text)
        mode = work_mode if work_mode else self._determine_work_mode(full_text, location)
        is_intern = self._is_internship(full_text)
        is_senior = self._is_senior(full_text)
        is_entry = self._is_entry_level(full_text)
        relevance = self._calculate_relevance(full_text)

        is_mumbai = any(loc in location.lower() for loc in self.EXCLUDED_LOCATIONS) or any(loc in description.lower() for loc in self.EXCLUDED_LOCATIONS)

        # 2. Logic Gates

        # Gate 1: Non-technical HR/Marketing etc.
        if "hr " in title.lower() or "marketing" in title.lower() or "finance" in title.lower():
             return self._reject("Wrong field", "Non-technical role")

        # Gate 2: Seniority (If it's senior, reject immediately)
        if is_senior and not is_intern:
            return self._reject("Senior/experienced", "Senior terms found in JD/Title")

        # Gate 3: Internships
        if is_intern:
            if mode == "REMOTE":
                return self._accept("REMOTE_INTERNSHIP", target_field, mode, "INTERNSHIP", relevance, "Remote internship")
            else:
                if is_mumbai:
                    return self._reject("Mumbai non-remote", "Onsite/Hybrid internship in Mumbai")
                return self._accept("NON_REMOTE_INTERNSHIP", target_field, mode, "INTERNSHIP", relevance, "Non-remote internship")

        # Gate 4: Full-Time
        if is_entry or not is_senior:
            if mode == "REMOTE":
                return self._accept("REMOTE_ENTRY_LEVEL_FULL_TIME", target_field, mode, "ENTRY_LEVEL", relevance, "Remote entry level full time")
            else:
                return self._reject("Non-internship/non-remote full-time", "Onsite/Hybrid full time roles are excluded unless they are internships")

        return self._reject("Other", "Fails all targeting logic")

    def _accept(self, role_type, field, mode, seniority, score, reason):
        return {
            "is_target_role": True,
            "role_type": role_type,
            "target_field": field,
            "work_mode": mode,
            "seniority": seniority,
            "relevance_score": score,
            "filter_reason": reason,
            "filter_confidence": 0.95
        }

    def _reject(self, rejection_category, reason):
        return {
            "is_target_role": False,
            "role_type": "REJECTED",
            "target_field": None,
            "work_mode": "UNKNOWN",
            "seniority": "UNKNOWN",
            "relevance_score": 0.0,
            "filter_reason": reason,
            "filter_confidence": 0.95,
            "rejection_category": rejection_category
        }
