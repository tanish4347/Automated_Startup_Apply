"""Cluster harvested form questions into canonical keys and write docs/QUESTIONS.md.

1. Rules: an ordered list of (canonical_key, pattern) over the normalised label. Country-bound
   questions (work authorisation, visa sponsorship) get the country in the key, because
   "authorised to work in the US" and "... in India" have different answers.
2. Labels no rule claims are grouped by identical normalised text, then groups whose word sets
   overlap strongly (Jaccard >= 0.8) are merged under custom.<slug>.
3. A cluster is UNSURE, and listed for a human to review, when it was merged by similarity, when a
   broad rule claimed it, when its members disagree on field type, or when they name different
   countries under one key. A wrong merge is how the system later answers confidently and wrongly.

Ranking is by weighted volume: each platform's weight is its share of the jobs that will actually
be applied to (active, not LinkedIn), and a cluster's volume on a platform is the share of that
platform's sampled companies whose form asks it. Raw counts would let the ATS sample dominate.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from autoapply.questions.harvest import normalize_label

# (key, pattern, broad). Order matters: first match wins. broad=True marks catch-all patterns
# whose matches are always reviewed by hand.
RULES: list[tuple[str, str, bool]] = [
    ("preferred_name", r"\b(preferred|nick)( first)? ?name\b", False),
    ("first_name", r"\b(first|given) name\b", False),
    ("last_name", r"\b(last|family|sur) ?name\b", False),
    ("full_name", r"^(your |full |legal )?(full )?name$|^legal name$", False),
    ("email", r"\be ?mail\b", False),
    ("phone", r"\b(phone|mobile|contact number|whatsapp)\b|^\d{1,3}$", False),  # "+91" = the phone field's country code
    ("resume", r"\b(resume|cv)\b", False),
    ("cover_letter", r"\bcover letter\b", False),
    ("linkedin_url", r"\blinkedin\b", False),
    ("github_url", r"\bgithub\b", False),
    ("twitter_url", r"\btwitter\b", False),
    ("portfolio_url", r"\bportfolio\b|\bwebsite\b|\bblog\b|personal (site|url)", False),
    ("visa_sponsorship", r"\bsponsor", False),
    ("work_authorization", r"authori[sz]ed to work|work authori[sz]ation|right to work|eligible to work|legally (able|permitted)", False),
    ("relocation", r"\brelocat", False),
    ("notice_period", r"\bnotice period\b", False),
    ("start_date", r"start date|when can you (start|join)|earliest (date|start)|available to (start|join)|joining date|availability", False),
    ("salary_expectation", r"\b(salary|compensation|ctc|stipend|pay) (expectation|requirement|expected)|expected (ctc|salary|stipend|compensation)|desired (salary|pay)|current ctc", False),
    ("how_heard", r"how did you (hear|find|learn|come)|where did you (hear|find|see)|\bsource\b", True),
    ("referral", r"\brefer(red|ral|rer)\b", False),
    ("previously_employed", r"(previously|ever|have you) (worked|been employed)|former employee|worked (here|for us|at)", False),
    ("graduation_date", r"graduat(ion|e|ing) (date|year|month)|year of (graduation|passing)|passing (out )?year|expected graduation", False),
    ("gpa", r"\b(c?gpa|grade point|percentage|cpi)\b", False),
    ("major", r"\b(major|field of study|discipline|speciali[sz]ation|branch|stream)\b", False),
    ("degree", r"\bdegree\b|highest (level of )?education|education level|qualification", True),
    ("university", r"\b(university|college|institute|institution|school)\b", True),
    ("years_experience", r"years? of (relevant |professional |work )?experience|how many years", False),
    ("current_company", r"current (company|employer|organi[sz]ation)", False),
    ("current_title", r"current (title|role|designation|position)", False),
    ("location_current", r"current(ly)? (location|city|based)|where are you (currently )?(located|based)|^location$|^city$|city of residence|place of residence", True),
    ("work_mode_ok", r"(willing|able|comfortable) to work (on ?site|in (the )?office|from (the )?office|remotely|hybrid)|in[- ]office|on[- ]?site", False),
    ("pronouns", r"\bpronoun", False),
    ("gender", r"\bgender\b|\bsex\b", False),
    ("race_ethnicity", r"\b(race|ethnic|hispanic|latin[oax])", False),
    ("veteran_status", r"veteran", False),
    ("disability_status", r"disabilit|differently abled|\bpwd\b|handicap", False),
    ("age_18_plus", r"\b18\b|over the age|legal age", False),
    ("languages", r"languages? (do you )?(speak|know)|language proficiency|fluent in|proficien(t|cy) in english", False),
    ("timezone", r"time ?zone", False),
    ("ai_policy_ack", r"\bai\b.*(policy|use|usage)|artificial intelligence", False),
    ("privacy_consent", r"privacy|consent|gdpr|data (processing|protection|retention)|i (agree|acknowledge|certify)", True),
    ("why_company", r"why (do you want|are you interested|us\b|this (role|company)|join|should we)|what (interests|excites|attracts) you", False),
    ("additional_info", r"additional information|anything else|message to (the )?hiring|note to", False),
]
_COMPILED = [(k, re.compile(p), broad) for k, p, broad in RULES]
COUNTRY_KEYS = {"work_authorization", "visa_sponsorship"}
COUNTRIES = {"united states": "us", "u s": "us", "us": "us", "usa": "us", "america": "us", "uk": "uk",
             "united kingdom": "uk", "india": "india", "canada": "canada", "germany": "germany",
             "european union": "eu", "eu": "eu", "australia": "australia", "singapore": "singapore",
             "ireland": "ireland", "netherlands": "netherlands", "france": "france", "japan": "japan"}
_COUNTRY_RE = re.compile(r"\b(" + "|".join(sorted(map(re.escape, COUNTRIES), key=len, reverse=True)) + r")\b")


def countries_in(norm_label: str) -> set[str]:
    return {COUNTRIES[m] for m in _COUNTRY_RE.findall(norm_label)}


# Profile fields are named, not asked about: "Email", "Resume/CV", "Current location". A long
# label that merely mentions one ("What's one thing we won't see on your resume?", a support
# scenario that says "email") is a different question, so these keys only claim short labels.
FIELD_KEYS = {"preferred_name", "first_name", "last_name", "full_name", "email", "phone", "resume", "cover_letter",
              "linkedin_url", "github_url", "twitter_url", "portfolio_url", "location_current", "current_company",
              "current_title", "university", "degree", "major", "pronouns", "gender"}
# graduation_date and gpa stay question keys: forms ask them as full sentences ("Include your intended
# graduation year for your current degree") and their patterns are specific enough.
FIELD_MAX_WORDS = 8
QUESTION_MAX_WORDS = 40


def rule_key(norm_label: str) -> tuple[str, bool] | None:
    words = len(norm_label.split())
    if words > QUESTION_MAX_WORDS:
        return None
    for key, pattern, broad in _COMPILED:
        if key in FIELD_KEYS and words > FIELD_MAX_WORDS:
            continue
        if pattern.search(norm_label):
            if key in COUNTRY_KEYS:
                cs = countries_in(norm_label)
                if len(cs) == 1:
                    key = f"{key}.{next(iter(cs))}"
            return key, broad
    return None


def _slug(text: str, words: int = 6) -> str:
    return "_".join(text.split()[:words])[:60] or "unlabelled"


@dataclass
class Cluster:
    key: str
    rows: list[Any] = field(default_factory=list)
    how: str = "rule"          # rule | exact | similarity
    broad: bool = False
    reasons: list[str] = field(default_factory=list)

    @property
    def phrasings(self) -> Counter:
        return Counter(r.raw_label for r in self.rows)

    @property
    def platforms(self) -> Counter:
        return Counter(r.platform for r in self.rows)

    @property
    def field_types(self) -> Counter:
        return Counter(r.field_type for r in self.rows)

    def companies(self, platform: str | None = None) -> set[tuple[str, str]]:
        return {(r.platform, r.company_key) for r in self.rows if platform is None or r.platform == platform}

    @property
    def required_rate(self) -> float:
        return sum(1 for r in self.rows if r.is_required) / len(self.rows) if self.rows else 0.0


_FAMILY = {"select": "choice", "multi_select": "choice", "boolean": "choice",
           "text": "free text", "textarea": "free text", "url": "free text", "email": "free text",
           "phone": "free text", "number": "free text", "location": "free text"}


def cluster_rows(rows: list[Any]) -> list[Cluster]:
    by_key: dict[str, Cluster] = {}
    loose: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        norm = normalize_label(r.raw_label)
        hit = rule_key(norm)
        if hit:
            key, broad = hit
            c = by_key.setdefault(key, Cluster(key, how="rule", broad=broad))
            c.rows.append(r)
        else:
            loose[norm].append(r)
    # identical normalised text -> one group; then merge near-identical groups
    groups = [(norm, set(norm.split()), members) for norm, members in loose.items()]
    merged: list[list[tuple[str, list[Any]]]] = []
    for norm, words, members in sorted(groups, key=lambda g: -len(g[2])):
        for bucket in merged:
            head_words = set(bucket[0][0].split())
            if words and head_words and len(words & head_words) / len(words | head_words) >= 0.8:
                bucket.append((norm, members))
                break
        else:
            merged.append([(norm, members)])
    for bucket in merged:
        key = "custom." + _slug(bucket[0][0])
        while key in by_key:
            key += "_"
        c = Cluster(key, how="similarity" if len(bucket) > 1 else "exact")
        for _, members in bucket:
            c.rows.extend(members)
        by_key[key] = c
    clusters = list(by_key.values())
    for c in clusters:
        if c.how == "similarity":
            c.reasons.append(f"merged {len(c.phrasings)} different phrasings by word overlap")
        if c.broad and len({normalize_label(p) for p in c.phrasings}) > 1:
            c.reasons.append("claimed by a broad rule; check every phrasing belongs")
        # Same question, different widget (yes/no vs dropdown, text vs phone input) is fine. A choice
        # question merged with a free-text one usually is not: flag it when the minority kind is >= 10%.
        fam = Counter(_FAMILY.get(t, t) for t in c.field_types.elements())
        if len(fam) > 1 and min(fam.values()) / sum(fam.values()) >= 0.10:
            c.reasons.append(f"answer kinds differ: {dict(fam)} ({dict(c.field_types)})")
        cs = set().union(*(countries_in(normalize_label(p)) for p in c.phrasings)) if c.rows else set()
        if len(cs) > 1:
            c.reasons.append(f"phrasings name different countries: {sorted(cs)}")
    return clusters


# ── weighting by the real source mix ─────────────────────────────────────────

ATS_PLATFORMS = ("greenhouse", "lever", "ashby", "smartrecruiters")
PLATFORM_SOURCES = ("internshala", "unstop", "naukri", "instahyre")
_ATS_URL = {p: re.compile(p if p != "smartrecruiters" else r"smartrecruiters") for p in ATS_PLATFORMS}


def platform_weights(session: Session) -> tuple[dict[str, float], dict[str, int]]:
    """Share of the jobs that will actually be applied to (active, not LinkedIn), per form
    platform. Company-ATS jobs are split across ATS platforms by their application URLs; ATS jobs
    whose platform is unknown are spread in the same proportions."""
    from autoapply.models.job import Job
    counts: Counter = Counter()
    ats_unknown = 0
    for source, url in session.query(Job.source, Job.application_url).filter(Job.is_active == 1, Job.source != "linkedin"):
        if source in PLATFORM_SOURCES:
            counts[source] += 1
            continue
        plat = next((p for p, rx in _ATS_URL.items() if rx.search(url or "")), None)
        if plat:
            counts[plat] += 1
        else:
            ats_unknown += 1
    known_ats = sum(counts[p] for p in ATS_PLATFORMS)
    for p in ATS_PLATFORMS:
        counts[p] += round(ats_unknown * counts[p] / known_ats) if known_ats else 0
    counts["ats_other_unharvested"] = ats_unknown if not known_ats else 0
    total = sum(counts.values()) or 1
    return {p: n / total for p, n in counts.items()}, dict(counts)


def weighted_volume(c: Cluster, weights: dict[str, float], sampled: dict[str, int]) -> float:
    return sum(weights.get(p, 0.0) * len(c.companies(p)) / sampled[p] for p in c.platforms if sampled.get(p))


# ── report ───────────────────────────────────────────────────────────────────

def build(session: Session) -> tuple[list[Cluster], dict[str, float], dict[str, int], dict[str, int]]:
    from autoapply.models.form_question import FormQuestion
    rows = session.query(FormQuestion).all()
    clusters = cluster_rows(rows)
    for c in clusters:
        for r in c.rows:
            r.canonical_key = c.key
    session.commit()
    sampled = Counter()
    for plat, company in {(r.platform, r.company_key) for r in rows}:
        sampled[plat] += 1
    weights, counts = platform_weights(session)
    return clusters, weights, counts, dict(sampled)


def render(clusters: list[Cluster], weights: dict[str, float], counts: dict[str, int], sampled: dict[str, int],
           notes: list[str] | None = None) -> str:
    ranked = sorted(clusters, key=lambda c: -weighted_volume(c, weights, sampled))
    covered = sum(w for p, w in weights.items() if sampled.get(p))
    out = ["# Application form questions", "",
           "Generated by `autoapply questions report` from the `form_question` table: every question here was",
           "seen on a real application form. Nothing is invented, and nothing was submitted to collect it.", "",
           "## Source mix used for ranking", "",
           "Weights are each platform's share of active jobs that will actually be applied to (LinkedIn excluded:",
           "read-only). A cluster's weighted volume = sum over platforms of weight x (share of that platform's",
           "sampled companies whose form asks it).", "",
           "| form platform | active jobs | weight | companies/forms sampled |", "|---|---:|---:|---:|"]
    for p, w in sorted(weights.items(), key=lambda kv: -kv[1]):
        if not counts.get(p):
            continue
        out.append(f"| {p} | {counts.get(p, 0)} | {w:.1%} | {sampled.get(p, 0) or '**not harvested**'} |")
    out += ["", f"**{covered:.0%} of the weighted volume is backed by harvested forms.** Platforms marked",
            "*not harvested* contribute nothing to the ranking below until their form is captured.", ""]
    if notes:
        out += ["## Harvest notes", ""] + [f"- {n}" for n in notes] + [""]
    out += [f"## Clusters ({len(clusters)}), ranked by weighted volume", "",
            "| # | canonical_key | weighted | required | companies | platforms | field types | phrasings merged |",
            "|---:|---|---:|---:|---:|---|---|---|"]
    line_drawn = False
    for i, c in enumerate(ranked, 1):
        wv = weighted_volume(c, weights, sampled)
        if not line_drawn and wv < 0.02:
            out.append("| | **── stopping line: below here, fewer than 2% (weighted) of companies ask ──** | | | | | | |")
            line_drawn = True
        phr = "; ".join(f"{p} ({n})" if n > 1 else p for p, n in c.phrasings.most_common(6))
        if len(c.phrasings) > 6:
            phr += f"; … +{len(c.phrasings) - 6} more"
        out.append(f"| {i} | `{c.key}`{' ⚠' if c.reasons else ''} | {wv:.1%} | {c.required_rate:.0%} | {len(c.companies())} | "
                   f"{', '.join(f'{p}:{len(c.companies(p))}' for p in c.platforms)} | "
                   f"{', '.join(c.field_types)} | {phr.replace('|', '/')} |")
    unsure = [c for c in ranked if c.reasons]
    out += ["", f"## Clusters to review by hand ({len(unsure)})", "",
            "Each of these could hide a wrong merge. Check every phrasing belongs to the key; split it if not.", ""]
    for c in unsure:
        out.append(f"### `{c.key}`")
        out += [f"- {r}" for r in c.reasons]
        out.append("- phrasings: " + "; ".join(f"\"{p}\"" for p, _ in c.phrasings.most_common(15)))
        out.append("")
    return "\n".join(out) + "\n"
