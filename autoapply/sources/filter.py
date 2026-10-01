import re
from dataclasses import replace

from autoapply.config import LocationPolicy, SearchConfig, load_search_config
from autoapply.sources.location import NormalizedLocation, find_cities, normalize_location

def extract_yoe(text: str) -> float | None:
    patterns = [
        r'(\d+)\s*[-to]+\s*(\d+)\s*years?',
        r'(\d+)\+?\s*years?',
        r'at least\s*(\d+)\s*years?'
    ]
    min_yoe = None
    for p in patterns:
        matches = re.finditer(p, text, re.IGNORECASE)
        for m in matches:
            groups = m.groups()
            val = float(groups[0])
            start = max(0, m.start() - 30)
            end = min(len(text), m.end() + 30)
            context = text[start:end].lower()
            if 'experience' in context or 'exp ' in context:
                if min_yoe is None or val < min_yoe:
                    min_yoe = val
    return min_yoe


# ── Pay detection ──────────────────────────────────────────────────────────

_CURRENCIES = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "$": "USD", "usd": "USD",
               "€": "EUR", "eur": "EUR", "£": "GBP", "gbp": "GBP"}
_CUR = r"(?:₹|\brs\.?|\binr\b|\$|\busd\b|€|\beur\b|£|\bgbp\b)"
_NUM = r"\d[\d,]*(?:\.\d+)?\s*(?:k\b|lakhs?\b|lpa\b|l\b)?"
_PERIOD = r"(?:\s*(?:/|per|a|p\.?)\s*(month|mo|week|wk|hour|hr|year|yr|annum)\b|\s*(pm)\b|\s*(lpa)\b)?"
_AMOUNT_RE = re.compile(
    rf"(?P<cur>{_CUR})\s*(?P<lo>{_NUM})(?:\s*(?:-|–|to)\s*{_CUR}?\s*(?P<hi>{_NUM}))?(?P<period>{_PERIOD})"
    rf"|(?P<lo2>{_NUM})\s*(?P<cur2>\binr\b|\brs\b|\busd\b)(?P<period2>{_PERIOD})"
    rf"|(?P<lo3>\d+(?:\.\d+)?\s*k)\s*(?P<period3>(?:/|per)\s*(?:month|mo)\b)",
    re.IGNORECASE,
)
_UNPAID_RE = re.compile(r"\bunpaid\b|\bno stipend\b|\bwithout (?:a |any )?stipend\b|\bvolunteer (?:role|position|basis)\b", re.I)
_PERF_RE = re.compile(r"\bperformance[- ]based\b", re.I)
_PAID_WORDS_RE = re.compile(r"\bstipend\b|\bsalary\b|\bpaid internship\b|\bcompensation range\b|\bhourly pay\b", re.I)


def _to_number(raw: str) -> float:
    t = raw.lower().replace(",", "").strip()
    mult = 1.0
    for suffix, m in (("lakhs", 1e5), ("lakh", 1e5), ("lpa", 1e5), ("k", 1e3), ("l", 1e5)):
        if t.endswith(suffix):
            t, mult = t[: -len(suffix)].strip(), m
            break
    return float(t) * mult


def _period(raw: str | None) -> str | None:
    if not raw:
        return None
    r = raw.lower()
    if "lpa" in r or "year" in r or "yr" in r or "annum" in r:
        return "year"
    if "week" in r or "wk" in r:
        return "week"
    if "hour" in r or "hr" in r:
        return "hour"
    if "mo" in r or "pm" in r:
        return "month"
    return None


def detect_pay(text: str) -> dict:
    """Classify pay as PAID / UNPAID / UNKNOWN and extract a stipend amount if present.

    "Performance based" with no fixed amount is UNKNOWN: it often means little or no pay.
    """
    out = {"pay_status": "UNKNOWN", "pay_evidence": "No clear compensation information found",
           "stipend_min": None, "stipend_max": None, "stipend_currency": None, "stipend_period": None}

    def snippet(m: re.Match) -> str:
        return text[max(0, m.start() - 20): m.end() + 20].strip()

    m = _UNPAID_RE.search(text)
    if m:
        out.update(pay_status="UNPAID", pay_evidence=snippet(m))
        return out

    m = _AMOUNT_RE.search(text)
    if m:
        lo = m.group("lo") or m.group("lo2") or m.group("lo3")
        cur = m.group("cur") or m.group("cur2")
        period = m.group("period") or m.group("period2") or m.group("period3")
        try:
            lo_v = _to_number(lo)
            hi_v = _to_number(m.group("hi")) if m.group("hi") else lo_v
        except ValueError:
            lo_v = hi_v = None
        if lo_v:  # "$0" or unparsable is not evidence of pay
            out.update(
                pay_status="PAID", pay_evidence=snippet(m),
                stipend_min=lo_v, stipend_max=hi_v,
                stipend_currency=_CURRENCIES.get(cur.lower().strip()) if cur else None,
                stipend_period=_period(period) or ("year" if "lpa" in lo.lower() else None),
            )
            return out

    m = _PERF_RE.search(text)
    if m:
        out.update(pay_evidence=snippet(m))
        return out

    m = _PAID_WORDS_RE.search(text)
    if m:
        out.update(pay_status="PAID", pay_evidence=snippet(m))
    return out


# ── Location policy ────────────────────────────────────────────────────────

def policy_fit(loc: NormalizedLocation, policy: LocationPolicy) -> str:
    """'ok', 'outside_policy' or 'unknown' for a normalized location. Nothing is rejected here."""
    if loc.work_mode == "remote":
        return "outside_policy" if policy.remote == "disallowed" else "ok"
    allowed = {c for city in policy.onsite_cities for c in find_cities(city)}
    if allowed.intersection(loc.cities):
        return "ok"
    if loc.work_mode == "unknown":
        return "unknown"
    return "outside_policy"


def location_fit(
    location: str | None, title: str | None, policy: LocationPolicy, work_mode: str | None = None,
) -> tuple[str, str]:
    """Return (location_fit, work_mode). A work_mode supplied by the source wins over text heuristics."""
    loc = normalize_location(location, title)
    if work_mode and work_mode != "unknown":
        loc = replace(loc, work_mode=work_mode)
    return policy_fit(loc, policy), loc.work_mode


# ── Role family, duration, apply channel ──────────────────────────────────

_ROLE_FAMILY_RULES = [  # first match wins
    ("data_eng", r"\bdata engineer|\bdata platform\b|\betl\b|\banalytics engineer"),
    ("ml", r"\bmachine learning\b|\bml\b|\bmlops\b|\bai\b|\bartificial intelligence\b|\bdeep learning\b"
           r"|\bnlp\b|\bcomputer vision\b|\bgen ?ai\b|\bllm\b"),
    ("ds", r"\bdata scien|\bdata analy|\banalytics\b|\bbusiness intelligence\b"),
    ("research", r"\bresearch"),
    ("swe", r"\bsoftware\b|\bsde\b|\bswe\b|\bbackend\b|\bback-end\b|\bfrontend\b|\bfront-end\b"
            r"|\bfull[ -]?stack\b|\bdeveloper\b|\bprogrammer\b|\bengineer|\bweb\b|\bmobile\b"
            r"|\bandroid\b|\bios\b|\bdevops\b|\bcloud\b|\bplatform\b"),
]


def classify_role_family(title: str | None) -> str:
    t = (title or "").lower()
    for family, pattern in _ROLE_FAMILY_RULES:
        if re.search(pattern, t):
            return family
    return "other"


_DURATION_RE = re.compile(r"(\d{1,2})(?:\s*(?:-|–|to)\s*(\d{1,2}))?[\s-]*months?\b", re.I)
_DURATION_CONTEXT_RE = re.compile(r"duration|intern|long|period|program", re.I)


def detect_duration_months(text: str | None) -> int | None:
    """Shortest stated internship length in months, e.g. 'Duration: 3-6 Months' -> 3."""
    t = text or ""
    for m in _DURATION_RE.finditer(t):
        window = t[max(0, m.start() - 40): m.end() + 25]
        n = int(m.group(1))
        if 1 <= n <= 12 and _DURATION_CONTEXT_RE.search(window):
            return n
    return None


_ATS_HOSTS = {
    "greenhouse": ("greenhouse.io",), "lever": ("lever.co",), "ashby": ("ashbyhq.com",),
    "workable": ("workable.com",), "smartrecruiters": ("smartrecruiters.com",),
    "recruitee": ("recruitee.com",), "keka": ("keka.com",), "zoho_recruit": ("zohorecruit.",),
    "darwinbox": ("darwinbox.",), "freshteam": ("freshteam.com",),
}
_BOARD_CHANNELS = ("internshala", "naukri", "wellfound", "unstop", "instahyre")


def detect_apply_channel(source: str | None, ats_platform: str | None, application_url: str | None) -> str:
    """How an application would be submitted. LinkedIn guest data can't tell Easy Apply from an
    external link, so LinkedIn jobs stay 'unknown' until a detail fetch resolves them."""
    url = (application_url or "").lower()
    if url.startswith("mailto:"):
        return "email"
    if "docs.google.com/forms" in url or "forms.gle" in url:
        return "google_form"
    if ats_platform in _ATS_HOSTS or any(h in url for hosts in _ATS_HOSTS.values() for h in hosts):
        return "ats_direct"
    for board in _BOARD_CHANNELS:
        if source == board or f"{board}." in url:
            return board
    return "unknown"


def evaluate_job(job_data: dict, cfg: SearchConfig | None = None) -> dict:
    cfg = cfg or load_search_config()
    title = str(job_data.get('title') or '').lower()
    desc = str(job_data.get('description_text') or '').lower()
    emp_type = str(job_data.get('employment_type') or '').lower()
    comp_text = str(job_data.get('compensation_text') or '').lower()
    
    full_text = f"{title} {desc} {comp_text}".lower()
    
    score = 0.0
    reject_reasons = []
    category = "NONE"
    
    # 1. TARGET FIELD CHECK
    target_keywords = cfg.target_keywords
    exclude_keywords = cfg.exclude_title_keywords

    if any(re.search(rf'\b{re.escape(k)}\b', title) for k in exclude_keywords):
        reject_reasons.append("Excluded due to non-technical keywords in title")
        
    is_target_field = False
    matched_target = next((k for k in target_keywords if re.search(rf'\b{re.escape(k)}\b', title)), None)
    if matched_target:
        is_target_field = True
        score += 20
    elif any(k in desc[:500] for k in ['machine learning', 'artificial intelligence', 'software engineer', 'data science']):
        is_target_field = True
        matched_target = 'found in description'
        score += 10
        
    if not is_target_field:
        reject_reasons.append("Does not match target technical fields")

    # 2. SENIORITY PRECEDENCE
    senior_pattern = re.compile(
        r'\bsenior\b|\bsr\.?\b|\bprincipal\b|\blead\b|\bmanager\b|\bdirector\b|\bhead\b|\bvp\b|\bchief\b|\bexpert\b|\bfellow\b|\biii\b|\biv\b|\bv\b|\bmid-level\b|\bmid\s*level\b|\bstaff\b|[4-9]\+?\s*years?',
        re.IGNORECASE
    )
    if senior_pattern.search(title):
        reject_reasons.append("Excluded due to senior role keywords (Seniority Precedence)")
        score -= 50

    # 3. YOE CLASSIFICATION
    yoe = extract_yoe(desc)
    if yoe is not None and yoe < 15:
        if yoe > 3:
            reject_reasons.append(f"Requires too much experience: {yoe} years")
            score -= 30
        elif yoe <= 2:
            score += 5

    # 4. INTERNSHIP SPECIFIC RULE
    # Strictly require internship indicators. Exclude entry-level full time.
    intern_pattern = re.compile(r'\bintern\b|\binternship\b|\bco-op\b|\bcoop\b|\bworking student\b', re.IGNORECASE)

    is_internship = bool(intern_pattern.search(title) or intern_pattern.search(emp_type) or intern_pattern.search(desc[:200]))
    
    if is_internship:
        category = 'INTERNSHIP'
        score += 50
    else:
        reject_reasons.append("Job is not explicitly an internship (Entry-level/Full-time excluded)")
        score -= 100

    # 5. PHD / DOCTORAL EXCLUSION
    phd_patterns = [
        r'\bphd students? only\b',
        r'pursuing ph\.?d\b',
        r'pursuing a ph\.?d\b',
        r'enrolled in a ph\.?d\b',
        r'doctoral research intern',
        r'phd research intern',
        r'doctoral candidates? only',
        r'postdoctoral',
        r'phd required',
        r'ph\.?d\.? is required',
        r'currently pursuing.*ph\.?d',
        r'must be.*ph\.?d',
        r'phd\s*(or|and)?\s*postdoc',
        r'doctoral student',
        r'phd candidate'
    ]
    phd_regex = re.compile('|'.join(phd_patterns), re.IGNORECASE)
    
    # But allow "bachelor's, master's or phd" 
    bachelors_phd_pattern = re.compile(r"bachelor.*?master.*?phd|phd.*?master.*?bachelor|any degree|undergraduate", re.IGNORECASE)
    
    if phd_regex.search(full_text):
        if not bachelors_phd_pattern.search(full_text):
            reject_reasons.append("Excluded: PhD/Doctoral-specific internship")
            score -= 100

    # 6. LOCATION POLICY (flag only; never rejects)
    loc = normalize_location(job_data.get('location'), job_data.get('title'))
    if job_data.get('work_mode') and job_data['work_mode'] != 'unknown':
        loc = replace(loc, work_mode=job_data['work_mode'])
    fit = policy_fit(loc, cfg.location_policy)

    # 7. PAY STATUS DETECTION
    pay = detect_pay(f"{job_data.get('title') or ''} {job_data.get('description_text') or ''} {job_data.get('compensation_text') or ''}")

    # Determine status
    if len(reject_reasons) > 0:
        cls_status = "AUTO_REJECT"
    elif score >= 50:
        cls_status = "AUTO_ACCEPT"
    else:
        cls_status = "REVIEW"

    return {
        "classification_status": cls_status,
        "classification_category": category,
        "classification_evidence": f"Score: {score}. Matches: {matched_target}. YOE: {yoe}.",
        "reject_reason": " | ".join(reject_reasons) if reject_reasons else None,
        "score": score,
        "location_fit": fit,
        "work_mode": loc.work_mode,
        "city": loc.city,
        "country": loc.country,
        "role_family": classify_role_family(job_data.get('title')),
        "duration_months": detect_duration_months(f"{job_data.get('title') or ''}\n{job_data.get('description_text') or ''}"),
        "apply_channel": detect_apply_channel(
            job_data.get('source'), job_data.get('ats_platform'), job_data.get('application_url'),
        ),
        **pay,
    }
