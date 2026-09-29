import re

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

def evaluate_job(job_data: dict) -> dict:
    title = str(job_data.get('title') or '').lower()
    desc = str(job_data.get('description_text') or '').lower()
    emp_type = str(job_data.get('employment_type') or '').lower()
    loc = str(job_data.get('location') or '').lower()
    comp_text = str(job_data.get('compensation_text') or '').lower()
    
    full_text = f"{title} {desc} {comp_text}".lower()
    
    score = 0.0
    reject_reasons = []
    category = "NONE"
    
    # 1. TARGET FIELD CHECK
    target_keywords = [
        'data science', 'machine learning', 'ml', 'ai', 'genai', 'artificial intelligence',
        'data analytic', 'nlp', 'computer vision', 'research',
        'data engineer', 'software engineer', 'backend', 'full stack',
        'frontend', 'developer', 'programmer', 'sde'
    ]
    
    exclude_keywords = [
        'marketing', 'sales', 'hr ', 'human resources', 'finance', 'operations', 
        'ops', 'design', 'legal', 'account', 'business', 'recruiter', 'customer success', 
        'support', 'seo', 'content', 'writer', 'event', 'talent', 'payroll', 
        'compliance', 'auditor', 'mechanical', 'electrical', 'civil'
    ]
    
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
    intern_pattern = re.compile(r'\bintern\b|\binternship\b|co-op|coop|\bworking student\b', re.IGNORECASE)

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

    # 6. MUMBAI RULE
    is_remote = 'remote' in loc or 'remote' in title or 'anywhere' in loc or 'distributed' in loc
    if is_internship and 'mumbai' in loc and not is_remote:
        reject_reasons.append("Excluded: Mumbai non-remote internship")
        score -= 100

    # 7. PAY STATUS DETECTION
    pay_status = 'UNKNOWN'
    pay_evidence = 'No clear compensation information found'
    
    if re.search(r'\b(unpaid|volunteer)\b', full_text):
        pay_status = 'UNPAID'
        m = re.search(r'.{0,20}(unpaid|volunteer).{0,20}', full_text)
        pay_evidence = m.group(0) if m else 'Mentions unpaid/volunteer'
    elif re.search(r'(\$|INR|EUR|GBP|\d+[kK]\b|stipend|salary|hourly\s*pay|paid\s*internship|compensation\s*range)', full_text):
        pay_status = 'PAID'
        m = re.search(r'.{0,30}(\$|INR|EUR|GBP|\d+[kK]\b|stipend|salary|paid\s*internship).{0,30}', full_text)
        pay_evidence = m.group(0) if m else 'Mentions money/salary/stipend indicators'

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
        "pay_status": pay_status,
        "pay_evidence": pay_evidence.strip()
    }


