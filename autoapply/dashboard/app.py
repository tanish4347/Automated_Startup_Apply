"""
AutoApply Dashboard v3 — clean rebuild.
All routes in one file. Server-rendered Jinja2. No SPA.
"""
from __future__ import annotations
import os
import shutil
import uuid
import json
from pathlib import Path
from datetime import datetime, timezone

from fastapi import FastAPI, Request, Depends, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, desc

from autoapply.config import get_settings
from autoapply.models.base import Base, engine_from_settings, get_session_factory
from autoapply.models.job import Job
from autoapply.models.application import Application, ApplicationStatus
from autoapply.models.vault import (
    VaultIdentity, VaultEducation, VaultEmployment,
    VaultProject, VaultSkill, VaultResume, VaultAnswer,
)

# ── paths ──────────────────────────────────────────────────────────────────
_HERE = Path(__file__).resolve().parent
TEMPLATES_DIR = _HERE / "templates"
STATIC_DIR = _HERE / "static"
STATIC_DIR.mkdir(exist_ok=True)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# ── db ─────────────────────────────────────────────────────────────────────
_engine = None
_SessionFactory = None

def _init_db():
    global _engine, _SessionFactory
    if _engine is None:
        s = get_settings()
        _engine = engine_from_settings(s.db.url, s.db.echo)
        _SessionFactory = get_session_factory(_engine)

def get_db():
    _init_db()
    db = _SessionFactory()
    try:
        yield db
    finally:
        db.close()

# ── helpers ────────────────────────────────────────────────────────────────
def _fmt(dt):
    if dt is None: return ""
    if hasattr(dt, "strftime"): return dt.strftime("%d %b %Y")
    return str(dt)

def _job_stats(db):
    total = db.query(Job).filter(Job.is_active == 1).count()
    internships = db.query(Job).filter(Job.is_active == 1, Job.classification_category == 'INTERNSHIP').count()
    entry_level = db.query(Job).filter(Job.is_active == 1, Job.classification_category == 'ENTRY_LEVEL').count()
    return {"total": total, "internships": internships, "entry_level": entry_level}

def _app_stats(db):
    out = {s.value: db.query(Application).filter(Application.status == s).count() for s in ApplicationStatus}
    out["total"] = sum(out.values())
    return out

# ── app factory ────────────────────────────────────────────────────────────
def create_app(settings=None):
    _init_db()
    app = FastAPI(title="AutoApply", version="3.0")
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    # ── DASHBOARD ──────────────────────────────────────────────────────────
    @app.get("/", response_class=HTMLResponse)
    def root(request: Request, db: Session = Depends(get_db)):
        return templates.TemplateResponse(request, "index.html", {
            "page": "dashboard",
            "job_stats": _job_stats(db),
            "app_stats": _app_stats(db),
            "recent_jobs": db.query(Job).filter(Job.is_active == 1).order_by(desc(Job.discovered_at)).limit(6).all(),
            "recent_apps": db.query(Application).order_by(desc(Application.updated_at)).limit(6).all(),
            "fmt": _fmt,
        })

    # ── JOBS ───────────────────────────────────────────────────────────────
    @app.get("/jobs", response_class=HTMLResponse)
    def jobs(request: Request, db: Session = Depends(get_db),
             q: str = "", source: str = "", category: str = "", page: int = 1):
        PER = 50
        query = db.query(Job).filter(Job.is_active == 1)
        if q:
            query = query.filter((Job.title.ilike(f"%{q}%")) | (Job.company.ilike(f"%{q}%")))
        if source:
            query = query.filter(Job.source == source)
        if category:
            query = query.filter(Job.classification_category == category)
        total = query.count()
        job_list = query.order_by(desc(Job.discovered_at)).offset((page-1)*PER).limit(PER).all()
        sources = [r[0] for r in db.query(Job.source).distinct().order_by(Job.source).all() if r[0]]
        return templates.TemplateResponse(request, "jobs.html", {
            "page": "jobs", "jobs": job_list, "total": total,
            "q": q, "source": source, "category": category,
            "cur_page": page, "pages": max(1, (total+PER-1)//PER), "sources": sources, "fmt": _fmt,
        })

    @app.get("/jobs/{job_id}", response_class=HTMLResponse)
    def job_detail(request: Request, job_id: int, db: Session = Depends(get_db)):
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job: raise HTTPException(404)
        apps = db.query(Application).filter(Application.job_id == job_id).order_by(desc(Application.created_at)).all()
        return templates.TemplateResponse(request, "job_detail.html", {
            "page": "jobs", "job": job, "applications": apps, "fmt": _fmt,
        })

    @app.post("/jobs/{job_id}/queue")
    def queue_job(job_id: int, db: Session = Depends(get_db)):
        a = db.query(Application).filter(Application.job_id == job_id).first()
        if a and a.status == ApplicationStatus.DISCOVERED:
            a.status = ApplicationStatus.QUEUED
            a.date_queued = datetime.now(timezone.utc)
            db.commit()
        return RedirectResponse(f"/jobs/{job_id}", status_code=303)

    # ── APPLICATIONS ───────────────────────────────────────────────────────
    @app.get("/applications", response_class=HTMLResponse)
    def applications(request: Request, db: Session = Depends(get_db),
                     status: str = "", q: str = "", page: int = 1):
        PER = 50
        query = db.query(Application)
        if status:
            try:
                st = ApplicationStatus(status)
                query = query.filter(Application.status == st)
            except ValueError:
                pass
        if q:
            query = query.filter((Application.company.ilike(f"%{q}%")) | (Application.role.ilike(f"%{q}%")))
        total = query.count()
        app_list = query.order_by(desc(Application.updated_at)).offset((page-1)*PER).limit(PER).all()
        return templates.TemplateResponse(request, "applications.html", {
            "page": "applications", "applications": app_list, "total": total,
            "cur_page": page, "pages": max(1, (total+PER-1)//PER),
            "statuses": [s.value for s in ApplicationStatus],
            "status_filter": status, "q": q, "app_stats": _app_stats(db), "fmt": _fmt,
        })

    @app.get("/applications/{app_id}", response_class=HTMLResponse)
    def application_detail(request: Request, app_id: int, db: Session = Depends(get_db)):
        a = db.query(Application).filter(Application.id == app_id).first()
        if not a: raise HTTPException(404)
        job = db.query(Job).filter(Job.id == a.job_id).first()
        return templates.TemplateResponse(request, "application_detail.html", {
            "page": "applications", "application": a, "job": job, "fmt": _fmt,
        })

    @app.post("/applications/{app_id}/status")
    def update_app_status(app_id: int, new_status: str = Form(...), db: Session = Depends(get_db)):
        a = db.query(Application).filter(Application.id == app_id).first()
        if a:
            try:
                a.status = ApplicationStatus(new_status)
                db.commit()
            except ValueError:
                pass
        return RedirectResponse(f"/applications/{app_id}", status_code=303)

    # ── QUEUE ──────────────────────────────────────────────────────────────
    @app.get("/queue", response_class=HTMLResponse)
    def queue(request: Request, db: Session = Depends(get_db)):
        queued = db.query(Application).filter(Application.status.in_([
            ApplicationStatus.QUEUED, ApplicationStatus.IN_PROGRESS, ApplicationStatus.FAILED,
        ])).order_by(Application.date_queued.asc()).all()
        job_map = {a.job_id: db.query(Job).filter(Job.id == a.job_id).first() for a in queued}
        return templates.TemplateResponse(request, "queue.html", {
            "page": "queue", "applications": queued, "job_map": job_map, "fmt": _fmt,
        })

    @app.post("/queue/start")
    def queue_start(db: Session = Depends(get_db)):
        discovered = db.query(Application).filter(Application.status == ApplicationStatus.DISCOVERED).order_by(Application.date_discovered.asc()).limit(50).all()
        for a in discovered:
            a.status = ApplicationStatus.QUEUED
            a.date_queued = datetime.now(timezone.utc)
        db.commit()
        return RedirectResponse("/queue", status_code=303)

    @app.post("/queue/retry-failed")
    def retry_failed(db: Session = Depends(get_db)):
        for a in db.query(Application).filter(Application.status == ApplicationStatus.FAILED).all():
            a.status = ApplicationStatus.QUEUED
            a.date_queued = datetime.now(timezone.utc)
        db.commit()
        return RedirectResponse("/queue", status_code=303)

    # ── COMPANIES ──────────────────────────────────────────────────────────
    @app.get("/companies", response_class=HTMLResponse)
    def companies(request: Request, db: Session = Depends(get_db)):
        rows = db.query(Job.company, func.count(Job.id).label("cnt")).filter(Job.is_active == 1, Job.company != None).group_by(Job.company).order_by(desc("cnt")).all()
        return templates.TemplateResponse(request, "companies.html", {"page": "companies", "companies": rows})

    @app.get("/companies/{company_name}", response_class=HTMLResponse)
    def company_detail(request: Request, company_name: str, db: Session = Depends(get_db)):
        jobs = db.query(Job).filter(Job.company == company_name, Job.is_active == 1).all()
        apps = db.query(Application).filter(Application.company == company_name).all()
        return templates.TemplateResponse(request, "company_detail.html", {
            "page": "companies", "company_name": company_name, "jobs": jobs, "applications": apps, "fmt": _fmt,
        })

    # ── PROFILE / VAULT ────────────────────────────────────────────────────
    @app.get("/profile", response_class=HTMLResponse)
    def profile(request: Request, db: Session = Depends(get_db), saved: str = ""):
        identity = db.query(VaultIdentity).first()
        answers = identity.answers if identity else []
        cv_answers = [a for a in answers if a.category == "CV Extracted"]
        
        # calculate completeness
        completeness = {
            "Personal": "Complete" if identity and identity.email and identity.first_name else "Needs Input",
            "Education": "Complete" if identity and identity.educations else "Needs Input",
            "Experience": "Complete" if identity and identity.employments else "Needs Input",
            "Projects": "Complete" if identity and identity.projects else "Needs Input",
            "Skills": "Complete" if identity and identity.skills else "Needs Input",
            "Resume": "Complete" if identity and identity.resumes else "Needs Input",
        }
        ans_filled = len([a for a in answers if a.answer and a.status != 'UNKNOWN'])
        ans_total = len(answers) if answers else 1
        completeness["Answers"] = f"{int((ans_filled / ans_total) * 100)}%" if answers else "0%"
        
        return templates.TemplateResponse(request, "profile.html", {
            "page": "profile",
            "identity": identity,
            "cv_answers": cv_answers,
            "completeness": completeness,
            "saved": saved,
        })

    @app.post("/profile/save")
    async def profile_save(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        identity = db.query(VaultIdentity).first()
        if not identity:
            identity = VaultIdentity()
            db.add(identity)
            db.flush()
        for f in ["first_name","middle_name","last_name","preferred_name","email","phone","city","country","pronouns",
                  "linkedin_url","github_url","portfolio_url","codeforces_url","leetcode_url","kaggle_url","google_scholar_url","other_links"]:
            setattr(identity, f, form.get(f, "").strip() or None)
        db.commit()
        return RedirectResponse("/profile?saved=1", status_code=303)

    @app.post("/profile/education/save")
    async def education_save(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        identity = db.query(VaultIdentity).first()
        if not identity: return RedirectResponse("/profile", status_code=303)
        eid = form.get("edu_id", "").strip()
        edu = db.query(VaultEducation).filter(VaultEducation.id == int(eid)).first() if eid else None
        if not edu:
            edu = VaultEducation(identity_id=identity.id)
            db.add(edu)
        for f in ["institution","degree","major","minor","specialization","start_date","end_date","cgpa","scale","enrollment_status"]:
            setattr(edu, f, form.get(f, "").strip() or None)
        db.commit()
        return RedirectResponse("/profile#education", status_code=303)

    @app.post("/profile/education/{edu_id}/delete")
    def education_delete(edu_id: int, db: Session = Depends(get_db)):
        edu = db.query(VaultEducation).filter(VaultEducation.id == edu_id).first()
        if edu: db.delete(edu); db.commit()
        return RedirectResponse("/profile#education", status_code=303)

    @app.post("/profile/experience/save")
    async def experience_save(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        identity = db.query(VaultIdentity).first()
        if not identity: return RedirectResponse("/profile", status_code=303)
        eid = form.get("emp_id", "").strip()
        emp = db.query(VaultEmployment).filter(VaultEmployment.id == int(eid)).first() if eid else None
        if not emp:
            emp = VaultEmployment(identity_id=identity.id)
            db.add(emp)
        for f in ["company","job_title","employment_type","start_date","end_date","description"]:
            setattr(emp, f, form.get(f, "").strip() or None)
        emp.is_current = bool(form.get("is_current"))
        db.commit()
        return RedirectResponse("/profile#experience", status_code=303)

    @app.post("/profile/experience/{emp_id}/delete")
    def experience_delete(emp_id: int, db: Session = Depends(get_db)):
        emp = db.query(VaultEmployment).filter(VaultEmployment.id == emp_id).first()
        if emp: db.delete(emp); db.commit()
        return RedirectResponse("/profile#experience", status_code=303)

    @app.post("/profile/project/save")
    async def project_save(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        identity = db.query(VaultIdentity).first()
        if not identity: return RedirectResponse("/profile", status_code=303)
        pid = form.get("proj_id", "").strip()
        proj = db.query(VaultProject).filter(VaultProject.id == int(pid)).first() if pid else None
        if not proj:
            proj = VaultProject(identity_id=identity.id)
            db.add(proj)
        for f in ["name","technologies","description","github_url","demo_url","results"]:
            setattr(proj, f, form.get(f, "").strip() or None)
        db.commit()
        return RedirectResponse("/profile#projects", status_code=303)

    @app.post("/profile/project/{proj_id}/delete")
    def project_delete(proj_id: int, db: Session = Depends(get_db)):
        proj = db.query(VaultProject).filter(VaultProject.id == proj_id).first()
        if proj: db.delete(proj); db.commit()
        return RedirectResponse("/profile#projects", status_code=303)

    @app.post("/profile/skills/save")
    async def skills_save(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        identity = db.query(VaultIdentity).first()
        if not identity: return RedirectResponse("/profile", status_code=303)
        db.query(VaultSkill).filter(VaultSkill.identity_id == identity.id).delete()
        cats = form.getlist("skill_category")
        vals = form.getlist("skill_value")
        for cat, val in zip(cats, vals):
            if cat.strip() or val.strip():
                db.add(VaultSkill(identity_id=identity.id, category=cat.strip(), name=val.strip()))
        db.commit()
        return RedirectResponse("/profile#skills", status_code=303)

    # ── RESUMES & DOCUMENTS ────────────────────────────────────────────────
    @app.get("/resumes", response_class=HTMLResponse)
    def resumes(request: Request, db: Session = Depends(get_db), saved: str = ""):
        identity = db.query(VaultIdentity).first()
        return templates.TemplateResponse(request, "resumes.html", {
            "page": "resumes", "resumes": identity.resumes if identity else [], "saved": saved, "fmt": _fmt,
        })

    @app.post("/profile/resume/upload")
    async def resume_upload(file: UploadFile = File(...), db: Session = Depends(get_db)):
        identity = db.query(VaultIdentity).first()
        if not identity:
            identity = VaultIdentity()
            db.add(identity); db.flush()
        os.makedirs("data/resumes", exist_ok=True)
        ext = Path(file.filename).suffix
        fpath = f"data/resumes/{uuid.uuid4()}{ext}"
        with open(fpath, "wb") as f:
            shutil.copyfileobj(file.file, f)
        for r in identity.resumes:
            r.is_active = False
        db.add(VaultResume(identity_id=identity.id, file_name=file.filename, file_path=fpath, is_active=True))
        db.commit()
        return RedirectResponse("/resumes?saved=1", status_code=303)

    @app.post("/resumes/{resume_id}/activate")
    def resume_activate(resume_id: int, db: Session = Depends(get_db)):
        identity = db.query(VaultIdentity).first()
        if identity:
            for r in identity.resumes:
                r.is_active = (r.id == resume_id)
            db.commit()
        return RedirectResponse("/resumes", status_code=303)

    @app.post("/resumes/{resume_id}/delete")
    def resume_delete(resume_id: int, db: Session = Depends(get_db)):
        r = db.query(VaultResume).filter(VaultResume.id == resume_id).first()
        if r:
            if r.file_path and os.path.exists(r.file_path):
                try: os.remove(r.file_path)
                except: pass
            db.delete(r); db.commit()
        return RedirectResponse("/resumes", status_code=303)

    # ── QUESTION VAULT ─────────────────────────────────────────────────────
    @app.get("/vault", response_class=HTMLResponse)
    def vault(request: Request, db: Session = Depends(get_db), view: str = "categories"):
        identity = db.query(VaultIdentity).first()
        answers = identity.answers if identity else []
        
        # Filter out CV data if needed, or group it separately
        grouped: dict = {}
        unanswered = []
        needs_review = []
        
        for a in sorted(answers, key=lambda x: (x.category or "", x.question or "")):
            if a.category == "CV Extracted":
                continue
                
            grouped.setdefault(a.category or "General", []).append(a)
            
            if not a.answer and a.status != 'CONFIRMED':
                unanswered.append(a)
            if a.status == 'NEEDS REVIEW':
                needs_review.append(a)
                
        # Calculate stats for categories
        cat_stats = []
        for cat, items in grouped.items():
            ans_count = sum(1 for i in items if i.answer)
            cat_stats.append({
                "name": cat,
                "total": len(items),
                "answered": ans_count,
                "needs_review": sum(1 for i in items if i.status == 'NEEDS REVIEW')
            })

        return templates.TemplateResponse(request, "vault.html", {
            "page": "vault", "grouped": grouped, "cat_stats": cat_stats,
            "unanswered": unanswered, "needs_review": needs_review, "view": view
        })

    @app.post("/vault/save")
    async def vault_save(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        identity = db.query(VaultIdentity).first()
        if not identity: return RedirectResponse("/vault", status_code=303)
        
        for key in [k for k in form.keys() if k.startswith("answer_")]:
            aid = int(key.replace("answer_", ""))
            a = db.query(VaultAnswer).filter(VaultAnswer.id == aid).first()
            if a:
                ans_val = form.get(key, "").strip()
                if ans_val:
                    a.answer = ans_val
                    a.source = "USER ENTERED"
                    a.status = "CONFIRMED"
                    
        db.commit()
        
        view = form.get("view", "categories")
        return RedirectResponse(f"/vault?view={view}", status_code=303)
        
    @app.post("/vault/confirm")
    async def vault_confirm(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        aid = int(form.get("answer_id", 0))
        a = db.query(VaultAnswer).filter(VaultAnswer.id == aid).first()
        if a:
            a.status = "CONFIRMED"
            a.source = "USER CONFIRMED"
            db.commit()
        return RedirectResponse("/profile", status_code=303)

    @app.post("/vault/add")
    async def vault_add(request: Request, db: Session = Depends(get_db)):
        form = await request.form()
        identity = db.query(VaultIdentity).first()
        if not identity:
            identity = VaultIdentity(); db.add(identity); db.flush()
        db.add(VaultAnswer(
            identity_id=identity.id,
            canonical_key=f"custom.{uuid.uuid4().hex[:8]}",
            category=form.get("category", "Custom"),
            question=form.get("question", ""),
            answer=form.get("answer", ""),
            source="USER ENTERED", sensitivity="NORMAL", status="CONFIRMED",
        ))
        db.commit()
        return RedirectResponse("/vault?view=categories", status_code=303)

    @app.post("/vault/{answer_id}/delete")
    def vault_delete(answer_id: int, db: Session = Depends(get_db)):
        a = db.query(VaultAnswer).filter(VaultAnswer.id == answer_id).first()
        if a: db.delete(a); db.commit()
        return RedirectResponse("/vault?view=categories", status_code=303)

    # ── DISCOVERY ──────────────────────────────────────────────────────────
    @app.get("/discovery", response_class=HTMLResponse)
    def discovery(request: Request, db: Session = Depends(get_db)):
        source_rows = db.query(Job.source, func.count(Job.id)).filter(Job.is_active == 1).group_by(Job.source).order_by(desc(func.count(Job.id))).all()
        return templates.TemplateResponse(request, "discovery.html", {
            "page": "discovery", "job_stats": _job_stats(db), "source_rows": source_rows,
        })

    # ── INTERVIEW PREP ─────────────────────────────────────────────────────
    @app.get("/interview-prep", response_class=HTMLResponse)
    def interview_prep(request: Request, db: Session = Depends(get_db)):
        active = db.query(Application).filter(Application.status.in_([
            ApplicationStatus.ASSESSMENT, ApplicationStatus.INTERVIEW,
        ])).all()
        return templates.TemplateResponse(request, "interview_prep.html", {
            "page": "interview-prep", "applications": active, "fmt": _fmt,
        })

    # ── SETTINGS ───────────────────────────────────────────────────────────
    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request, db: Session = Depends(get_db)):
        identity = db.query(VaultIdentity).first()
        active_resume = next((r for r in identity.resumes if r.is_active), None) if identity else None
        return templates.TemplateResponse(request, "settings.html", {
            "page": "settings",
            "job_count": db.query(Job).filter(Job.is_active == 1).count(),
            "app_count": db.query(Application).count(),
            "identity": identity, "active_resume": active_resume,
        })

    return app
