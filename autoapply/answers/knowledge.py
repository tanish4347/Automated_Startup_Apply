from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass, field
from sqlalchemy.orm import Session
from datetime import timezone

from autoapply.models.vault import VaultIdentity, VaultPolicy, VaultAnswer, VaultEmployment, VaultEducation
from autoapply.models.approved_answer import ApprovedAnswerCache
from autoapply.candidate.sensitive import is_sensitive_key, SENSITIVE, USER

@dataclass
class KnowledgeEntry:
    canonical_key: str
    category: str
    value: Any
    value_type: str
    source: str           # FACT, DERIVED, POLICY, APPROVED_CACHE, LLM, USER_INPUT
    confidence: float
    sensitive: bool
    derivable: bool
    conditions: Dict[str, Any] = field(default_factory=dict)
    question_variants: List[str] = field(default_factory=list)
    allowed_options: Optional[List[str]] = None
    approval_status: str = "CONFIRMED"
    last_verified: Optional[datetime.datetime] = None

class CandidateKnowledgeBase:
    def __init__(self, session: Session):
        self.session = session
        
    def get_identity(self) -> VaultIdentity | None:
        return self.session.query(VaultIdentity).first()

    def resolve(self, canonical_key: str) -> Optional[KnowledgeEntry]:
        from autoapply.candidate.concepts import concept_for

        # Policies and generic confirmed answers remain resolvable before a full identity record
        # exists (for example in a freshly initialized intake session).
        identity = self.get_identity() or VaultIdentity()

        # 1. FACTS (Direct properties)
        # Mapping generic factual canonical keys to identity properties
        fact_mapping = {
            "email": "email",
            "phone": "phone",
            "first_name": "first_name",
            "last_name": "last_name",
            "full_name": "full_name",
            "linkedin_url": "linkedin_url",
            "github_url": "github_url",
            "portfolio_url": "portfolio_url",
            "resume": "resume_path",
        }

        # Handle simple facts
        if canonical_key in fact_mapping:
            val = getattr(identity, fact_mapping[canonical_key], None)
            if val is not None:
                return KnowledgeEntry(
                    canonical_key=canonical_key,
                    category="identity",
                    value=val,
                    value_type="string",
                    source="FACT",
                    confidence=1.0,
                    sensitive=False,
                    derivable=False
                )

        if canonical_key == "location_current" or canonical_key == "location.current.city":
            if identity.city and identity.country:
                val = f"{identity.city}, {identity.country}"
            else:
                val = identity.city or identity.country
            if val is not None:
                return KnowledgeEntry(
                    canonical_key=canonical_key,
                    category="location",
                    value=val,
                    value_type="string",
                    source="FACT",
                    confidence=1.0,
                    sensitive=False,
                    derivable=False
                )
        if canonical_key == "location.permanent.city" and (identity.permanent_city or identity.permanent_country):
            val = ", ".join(x for x in (identity.permanent_city, identity.permanent_country) if x)
            if identity.status == "CONFIRMED":
                return KnowledgeEntry(canonical_key, "location", val, "string", "FACT", 1.0, False, False)

        education_fields = {
            "education.university": "institution", "education.degree_title": "degree",
            "education.degree_level": "degree_level", "education.major": "major",
            "education.specialization": "specialization", "education.department": "department",
        }
        attr = education_fields.get(canonical_key)
        if attr:
            for ed in reversed(identity.educations):
                val = getattr(ed, attr, None)
                if val and ed.status == "CONFIRMED":
                    return KnowledgeEntry(canonical_key, "education", val, "string", "FACT", 1.0,
                                          False, False)

        # 2. DERIVED FACTS
        if canonical_key == "years_experience" or canonical_key == "experience.total_years":
            total_months = 0
            for emp in identity.employments:
                if emp.start_date:
                    try:
                        # naive parse, just year for now if string
                        start_year = int(emp.start_date.split()[-1]) if " " in emp.start_date else int(emp.start_date[:4])
                        end_year = datetime.datetime.now().year
                        if emp.end_date and not emp.is_current:
                            end_year = int(emp.end_date.split()[-1]) if " " in emp.end_date else int(emp.end_date[:4])
                        if end_year >= start_year:
                            total_months += (end_year - start_year) * 12
                    except Exception:
                        pass
            if total_months >= 0:
                years = max(0, total_months // 12)
                return KnowledgeEntry(
                    canonical_key=canonical_key,
                    category="experience",
                    value=str(years),
                    value_type="number",
                    source="DERIVED",
                    confidence=1.0,
                    sensitive=False,
                    derivable=True
                )
        
        if canonical_key == "graduation_date" or canonical_key == "education.current.expected_graduation":
            # get most recent or current education
            for ed in reversed(identity.educations):
                if ed.end_date:
                    return KnowledgeEntry(
                        canonical_key=canonical_key,
                        category="education",
                        value=ed.end_date,
                        value_type="date",
                        source="DERIVED",
                        confidence=1.0,
                        sensitive=True,
                        derivable=True
                    )
        if canonical_key == "experience.internship_count":
            internships = [e for e in identity.employments if "intern" in (e.job_title or "").lower() or "intern" in (e.employment_type or "").lower()]
            return KnowledgeEntry(
                canonical_key=canonical_key, category="experience", value=str(len(internships)),
                value_type="number", source="DERIVED", confidence=1.0, sensitive=False, derivable=True
            )
            
        if canonical_key == "experience.project_count":
            return KnowledgeEntry(
                canonical_key=canonical_key, category="experience", value=str(len(identity.projects)),
                value_type="number", source="DERIVED", confidence=1.0, sensitive=False, derivable=True
            )
            
        if canonical_key == "education.current.degree":
            for ed in reversed(identity.educations):
                if ed.degree:
                    return KnowledgeEntry(
                        canonical_key=canonical_key, category="education", value=ed.degree,
                        value_type="string", source="DERIVED", confidence=1.0, sensitive=False, derivable=True
                    )
        
        # 3. POLICIES
        policy = self.session.query(VaultPolicy).filter_by(key=canonical_key).first()
        if policy and policy.value is not None:
            return KnowledgeEntry(
                canonical_key=canonical_key,
                category="policy",
                value=policy.value,
                value_type="structured",
                source="POLICY",
                confidence=1.0,
                sensitive=False,
                derivable=False,
                conditions={"policy_rule": True}
            )

        # The legacy minimum-stipend policy has an explicit INR/month unit. It can answer only
        # the matching minimum-stipend concept; it never becomes salary, annual CTC, or stipend
        # actually received.
        if canonical_key == "compensation.minimum_stipend":
            policy = self.session.query(VaultPolicy).filter_by(key="stipend_floor").first()
            value = policy.value if policy and policy.status == "CONFIRMED" and policy.source == USER else None
            if value and value.get("period") == "month" and value.get("amount") is not None:
                return KnowledgeEntry(canonical_key, "compensation", value, "money", "POLICY", 1.0,
                                      True, False, conditions={"currency": value.get("currency"), "period": "month"})
            
        # Also check VaultAnswer for policies/preferences or facts stored there
        # but with proper provenance tracking
        keys_to_check = [canonical_key]
        if "." in canonical_key and not canonical_key.startswith("custom."):
            keys_to_check.append(canonical_key.split(".")[0])
            
        for k in keys_to_check:
            a = self.session.query(VaultAnswer).filter_by(canonical_key=k).first()
            if a and a.answer and a.status == "CONFIRMED" and a.scope not in ("application", "legacy"):
                concept = concept_for(k)
                if concept and concept.scope == "application":
                    continue
                now = datetime.datetime.now(datetime.timezone.utc)
                if a.valid_from and a.valid_from > now:
                    continue
                if a.valid_until and a.valid_until < now:
                    continue
                sensitive = is_sensitive_key(k) or a.sensitivity == SENSITIVE
                if sensitive and a.source != USER:
                    continue # sensitive answers must be user entered
                    
                prov = "USER_INPUT" if a.source == USER else "FACT"
                if a.category == "policy":
                    prov = "POLICY"
                return KnowledgeEntry(
                    canonical_key=k,
                    category=a.category or "generic",
                    value=a.value_json if a.value_json is not None else a.answer,
                    value_type=a.value_type or (concept_for(k).value_type if concept_for(k) else "string"),
                    source=prov,
                    confidence=a.confidence if a.confidence is not None else 1.0,
                    sensitive=sensitive,
                    derivable=False,
                    conditions=a.conditions or {},
                    last_verified=a.confirmed_at,
                )

        # Compatibility with confirmed legacy facts whose meaning is exact. Deliberately do not
        # map legacy `degree` onto either degree level or title, and do not map generic pay keys.
        legacy_keys = {
            "education.university": "university",
            "education.degree_title": "degree",
            "education.major": "major",
        }
        legacy_key = legacy_keys.get(canonical_key)
        if legacy_key:
            old = self.session.query(VaultAnswer).filter_by(canonical_key=legacy_key).first()
            if old and old.answer and old.status == "CONFIRMED" and old.source != "AI GENERATED":
                return KnowledgeEntry(canonical_key, "education", old.answer, "string", "FACT", 1.0,
                                      False, False)

        # 4. APPROVED ANSWERS CACHE
        cache_hit = self.session.query(ApprovedAnswerCache).filter_by(canonical_concept=canonical_key).order_by(ApprovedAnswerCache.id.desc()).first()
        if cache_hit:
            return KnowledgeEntry(
                canonical_key=canonical_key,
                category="story",
                value=cache_hit.answer,
                value_type="string",
                source="APPROVED_CACHE",
                confidence=1.0,
                sensitive=False,
                derivable=False
            )

        return None

