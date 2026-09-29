"""Parse a resume (PDF or plain text) into Vault facts the candidate then confirms.

Deterministic, no LLM: text comes from pdfplumber (x_tolerance=1.5; the default merges the
tightly-kerned words of LaTeX resumes into "DevelopedLLM-poweredagentic"), links from the PDF's
own link annotations. Sections are found by their headings; each section has a small parser for
the usual one-page layout:

    Education     Institution ............ score        (7.78/10, 91.6%, AIR 12812 | 75.16%)
                  Degree, Branch | Minor in X ... dates
    Internships   Title | area ................. dates
                  Company (| location)
                  • bullets
    Projects      Name | stack | GitHub ........ dates
                  • bullets
    Skills        Category: a, b, c

import_cv() writes everything with source='CV', status='NEEDS REVIEW'. Grades and graduation dates
are SENSITIVE (autoapply/candidate/sensitive.py): they are stored only as suggestions on the
matching catalog answers, never as facts; the candidate confirms them by their own input.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from autoapply.logging import get_logger

log = get_logger(__name__)

CV = "CV"
NEEDS_REVIEW = "NEEDS REVIEW"

_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
_DATE = rf"(?:{_MONTH}\s+)?\d{{4}}"
DATE_RANGE = re.compile(rf"({_DATE})\s*[–—-]\s*({_DATE}|Present|Current|Now|Ongoing)\s*$", re.I)
EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
PHONE = re.compile(r"(?:\+?\d{1,3}[\s-]?)?\d{10}\b|\+?\d{1,3}[\s-]\d{3,5}[\s-]?\d{3,5}[\s-]?\d{0,5}")
SCORE = re.compile(r"(\d{1,2}(?:\.\d{1,2})?)\s*/\s*(10|4(?:\.0)?)\s*$|(\d{2,3}(?:\.\d{1,2})?)\s*%\s*$")

HEADINGS = {
    "education": "education", "academic details": "education", "academics": "education",
    "internships": "experience", "internship": "experience", "experience": "experience",
    "work experience": "experience", "professional experience": "experience", "employment": "experience",
    "projects": "projects", "academic projects": "projects", "key projects": "projects",
    "skills": "skills", "skills and expertise": "skills", "technical skills": "skills",
    "skills & expertise": "skills",
    "awards and achievements": "other", "achievements": "other", "positions of responsibility": "other",
    "extra curricular activities": "other", "extracurricular activities": "other", "publications": "other",
    "certifications": "other", "coursework": "other", "courses": "other",
}


@dataclass
class Education:
    institution: str
    degree: str | None = None
    major: str | None = None
    minor: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    score: str | None = None       # "7.78" / "91.6"
    scale: str | None = None       # "10" / "%"


@dataclass
class Role:
    title: str
    company: str | None = None
    area: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    is_current: bool = False
    bullets: list[str] = field(default_factory=list)


@dataclass
class Project:
    name: str
    technologies: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    github_url: str | None = None
    bullets: list[str] = field(default_factory=list)


@dataclass
class ParsedCV:
    name: str | None = None
    email: str | None = None
    phone: str | None = None
    city: str | None = None
    links: dict[str, str] = field(default_factory=dict)      # linkedin / github / portfolio / other
    education: list[Education] = field(default_factory=list)
    experience: list[Role] = field(default_factory=list)
    projects: list[Project] = field(default_factory=list)
    skills: dict[str, list[str]] = field(default_factory=dict)


# ── text extraction ──────────────────────────────────────────────────────────

def extract(path: Path) -> tuple[str, list[str]]:
    """(text, link URIs in page order)."""
    if path.suffix.lower() != ".pdf":
        text = path.read_text(encoding="utf-8", errors="replace")
        return text, re.findall(r"https?://\S+", text)
    import pdfplumber
    texts, links = [], []
    with pdfplumber.open(str(path)) as pdf:
        for page in pdf.pages:
            texts.append(page.extract_text(x_tolerance=1.5) or "")
            links += [a["uri"] for a in (page.annots or []) if a.get("uri")]
    return "\n".join(texts), links


# ── parsing ──────────────────────────────────────────────────────────────────

def _heading(line: str) -> str | None:
    key = re.sub(r"[^a-z& ]", "", line.lower()).strip()
    return HEADINGS.get(key)


def split_sections(lines: list[str]) -> tuple[list[str], dict[str, list[str]]]:
    header, sections, current = [], {}, None
    for line in lines:
        kind = _heading(line)
        if kind:
            current = kind
            sections.setdefault(kind, [])
            continue
        (sections[current] if current else header).append(line)
    return header, sections


def _dates(line: str) -> tuple[str, str | None, str | None]:
    m = DATE_RANGE.search(line)
    if not m:
        return line.strip(), None, None
    return line[: m.start()].strip(), m.group(1), m.group(2)


def _is_bullet(line: str) -> bool:
    return line.lstrip().startswith(("•", "-", "–", "*", "◦", "▪"))


_SCHOOL = re.compile(r"\b(X|XII|HSC|SSC|ICSE|ISC|CBSE|class \d+|10th|12th)\b", re.I)


def parse_education(lines: list[str]) -> list[Education]:
    out: list[Education] = []
    i = 0
    while i < len(lines):
        first = lines[i].strip()
        if not first or _is_bullet(first):
            i += 1
            continue
        edu = Education(institution=first)
        m = SCORE.search(first)
        if m:
            edu.institution = first[: m.start()].strip(" |")
            edu.score, edu.scale = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), "%")
            # "AIR 12812 | 75.16%": keep the rank out of the institution name
            edu.institution = re.sub(r"\s+AIR\s+\d+\s*\|?\s*$", "", edu.institution).strip(" |")
        if i + 1 < len(lines) and not _is_bullet(lines[i + 1]):
            rest, edu.start_date, edu.end_date = _dates(lines[i + 1])
            parts = [p.strip() for p in rest.split("|") if p.strip()]
            school = next((p for p in parts if _SCHOOL.search(p)), None)
            if school:  # "JEE Advance | XII(HSC)": the board/class is the degree; exams are not a branch
                edu.degree = school
            elif parts:
                if "," in parts[0]:
                    edu.degree, edu.major = (x.strip() for x in parts[0].split(",", 1))
                else:
                    edu.degree = parts[0]
            for p in parts:
                if p.lower().startswith("minor"):
                    edu.minor = re.sub(r"^minor( in)?\s*", "", p, flags=re.I)
            i += 2
        else:
            i += 1
        out.append(edu)
    return out


def parse_roles(lines: list[str]) -> list[Role]:
    out: list[Role] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if _is_bullet(s):
            if out:
                out[-1].bullets.append(s.lstrip("•-–*◦▪ ").strip())
            continue
        head, start, end = _dates(s)
        if start:  # "Title | area ... dates" opens a new role
            title, _, area = head.partition("|")
            out.append(Role(title=title.strip(), area=area.strip() or None, start_date=start, end_date=end,
                            is_current=bool(end and re.match(r"present|current|now|ongoing", end, re.I))))
        elif out and out[-1].company is None and not out[-1].bullets:
            # "NirveonX Omnicare Pvt. Ltd." or "Department of Geology and Geophysics | IIT Kharagpur":
            # keep every part, the second is as often the parent organisation as a location
            out[-1].company = ", ".join(p.strip() for p in s.split("|") if p.strip())
        elif out and out[-1].bullets:  # a wrapped bullet
            out[-1].bullets[-1] += " " + s
    return out


def parse_projects(lines: list[str], links: list[str]) -> list[Project]:
    repo_links = [u for u in links if re.search(r"github\.com/[^/]+/[^/?#]+", u)]
    out: list[Project] = []
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if _is_bullet(s):
            if out:
                out[-1].bullets.append(s.lstrip("•-–*◦▪ ").strip())
            continue
        head, start, end = _dates(s)
        if start or not out:
            parts = [p.strip() for p in head.split("|")]
            has_repo = any(p.lower() in ("github", "code", "repo") for p in parts)
            parts = [p for p in parts if p.lower() not in ("github", "code", "repo", "link", "demo")]
            proj = Project(name=parts[0], technologies=", ".join(parts[1:]) or None, start_date=start, end_date=end)
            if has_repo and repo_links:
                proj.github_url = repo_links.pop(0)
            out.append(proj)
        elif out[-1].bullets:
            out[-1].bullets[-1] += " " + s
    return out


def parse_skills(lines: list[str]) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for line in lines:
        if ":" not in line or _is_bullet(line):
            continue
        cat, _, items = line.partition(":")
        vals = [v.strip() for v in re.split(r",|;", items) if v.strip()]
        if vals:
            out[cat.strip()] = vals
    return out


def parse_text(text: str, links: list[str] | None = None) -> ParsedCV:
    links = links or []
    lines = [l.rstrip() for l in text.splitlines() if l.strip()]
    header, sections = split_sections(lines)
    cv = ParsedCV()
    if header:
        cv.name = header[0].strip()
    contact = " | ".join(header[1:4])
    if (m := EMAIL.search(contact)):
        cv.email = m.group(0)
    if (m := PHONE.search(contact)):
        cv.phone = re.sub(r"\s+", "", m.group(0))
    for uri in links + re.findall(r"(?:https?://)?(?:www\.)?(?:linkedin|github)\.com/[\w./-]+", contact):
        if uri.startswith("mailto:"):
            cv.email = cv.email or uri.removeprefix("mailto:")
            continue
        u = uri if uri.startswith("http") else "https://" + uri
        if "linkedin.com/in/" in u:
            cv.links.setdefault("linkedin", u)
        elif re.search(r"github\.com/[^/]+/?$", u):
            cv.links.setdefault("github", u.rstrip("/"))
        elif u.startswith("mailto:"):
            cv.email = cv.email or u.removeprefix("mailto:")
        elif "github.com" not in u:
            cv.links.setdefault("portfolio", u)
    cv.education = parse_education(sections.get("education", []))
    cv.experience = parse_roles(sections.get("experience", []))
    cv.projects = parse_projects(sections.get("projects", []), links)
    cv.skills = parse_skills(sections.get("skills", []))
    return cv


def parse_file(path: str | Path) -> ParsedCV:
    text, links = extract(Path(path))
    return parse_text(text, links)


# ── writing to the Vault ─────────────────────────────────────────────────────

# catalog key -> how to fill it from the CV. Only NON-sensitive keys are answered; SENSITIVE ones
# (gpa, graduation_date, start dates ...) get a suggestion the candidate confirms.
def _answers_from(cv: ParsedCV) -> dict[str, str]:
    first, _, last = (cv.name or "").partition(" ")
    edu = cv.education[0] if cv.education else None
    current = next((r for r in cv.experience if r.is_current), None)
    out = {
        "full_name": cv.name, "first_name": first or None, "last_name": last.split()[-1] if last else None,
        "email": cv.email, "phone": cv.phone, "location_current": cv.city,
        "linkedin_url": cv.links.get("linkedin"), "github_url": cv.links.get("github"),
        "portfolio_url": cv.links.get("portfolio"),
        "university": edu.institution if edu else None,
        "degree": edu.degree if edu else None,
        "major": edu.major if edu else None,
        "current_company": current.company if current else None,
        "current_title": current.title if current else None,
        # Unstop's registration form: "Skills" and "Organization Name" (a student's college)
        "custom.skills": ", ".join(v for vals in cv.skills.values() for v in vals) if cv.skills else None,
        "custom.organization_name": edu.institution if edu else None,
    }
    return {k: v for k, v in out.items() if v}


def _suggestions_from(cv: ParsedCV) -> dict[str, str]:
    edu = cv.education[0] if cv.education else None
    out = {}
    if edu and edu.score:
        out["gpa"] = f"{edu.score}/{edu.scale}" if edu.scale != "%" else f"{edu.score}%"
    if edu and edu.end_date:
        out["graduation_date"] = edu.end_date
    return out


def import_cv(session: Session, path: str | Path) -> dict[str, Any]:
    """Parse the CV and write it to the Vault (source CV, NEEDS REVIEW). Idempotent: rows that
    already exist (same institution / company+title / project / skill) are not duplicated, and a
    confirmed or user-entered value is never overwritten."""
    from autoapply.models.vault import (
        VaultAnswer, VaultEducation, VaultEmployment, VaultIdentity, VaultProject, VaultResume, VaultSkill,
    )
    path = Path(path)
    cv = parse_file(path)
    ident = session.query(VaultIdentity).first()
    if ident is None:
        ident = VaultIdentity(source=CV, status=NEEDS_REVIEW)
        session.add(ident)
    first, _, last = (cv.name or "").partition(" ")
    for col, val in (("first_name", first), ("last_name", last), ("email", cv.email), ("phone", cv.phone),
                     ("city", cv.city), ("linkedin_url", cv.links.get("linkedin")),
                     ("github_url", cv.links.get("github")), ("portfolio_url", cv.links.get("portfolio"))):
        if val and not getattr(ident, col):
            setattr(ident, col, val)
    session.flush()

    counts = {"education": 0, "experience": 0, "projects": 0, "skills": 0, "answers": 0, "suggestions": 0}
    for e in cv.education:
        if not any(x.institution == e.institution for x in ident.educations):
            # cgpa / scale / end_date are SENSITIVE: left for the candidate to enter.
            session.add(VaultEducation(identity=ident, institution=e.institution, degree=e.degree, major=e.major,
                                       minor=e.minor, start_date=e.start_date, source=CV, status=NEEDS_REVIEW))
            counts["education"] += 1
    for r in cv.experience:
        if not any(x.company == r.company and x.job_title == r.title for x in ident.employments):
            session.add(VaultEmployment(identity=ident, company=r.company, job_title=r.title,
                                        employment_type="Internship" if "intern" in r.title.lower() else None,
                                        start_date=r.start_date, end_date=r.end_date, is_current=r.is_current,
                                        description="\n".join(f"- {b}" for b in r.bullets) or None,
                                        source=CV, status=NEEDS_REVIEW))
            counts["experience"] += 1
    for p in cv.projects:
        if not any(x.name == p.name for x in ident.projects):
            session.add(VaultProject(identity=ident, name=p.name, technologies=p.technologies, github_url=p.github_url,
                                     description="\n".join(f"- {b}" for b in p.bullets) or None,
                                     source=CV, status=NEEDS_REVIEW))
            counts["projects"] += 1
    for cat, names in cv.skills.items():
        if not any(x.category == cat for x in ident.skills):
            session.add(VaultSkill(identity=ident, category=cat, name=", ".join(names), source=CV, status=NEEDS_REVIEW))
            counts["skills"] += 1
    if not any(r.file_path == str(path.resolve()) for r in ident.resumes):
        for r in ident.resumes:
            r.is_active = False
        session.add(VaultResume(identity=ident, file_name=path.name, file_path=str(path.resolve()), is_active=True))

    answers = {a.canonical_key: a for a in session.query(VaultAnswer).all()}
    for a in answers.values():          # catalog rows seeded before any identity existed
        if a.identity_id is None:
            a.identity = ident
    for key, value in _answers_from(cv).items():
        row = answers.get(key)
        if row is not None and not row.answer and row.sensitivity != "SENSITIVE":
            row.answer, row.source, row.status, row.intake_pass = value, CV, NEEDS_REVIEW, "cv"
            row.identity = ident
            counts["answers"] += 1
    resume = answers.get("resume")
    if resume is not None and not resume.answer:
        resume.answer, resume.source, resume.status, resume.intake_pass = path.name, CV, NEEDS_REVIEW, "cv"
        counts["answers"] += 1
    for key, value in _suggestions_from(cv).items():
        row = answers.get(key)
        if row is not None and not row.answer:
            row.suggested_answer = value
            row.intake_pass = "cv"
            counts["suggestions"] += 1
    session.commit()
    log.info("cv_imported", path=str(path), **counts)
    return counts
