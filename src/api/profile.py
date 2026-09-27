from fastapi import APIRouter, Request, Depends, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
import os
import aiofiles

from src.db.session import get_db
from src.models.candidate import CandidateProfileExt, SensitiveInfo, Document
from src.core.security import encrypt_value, mask_sensitive
from src.core.completeness import ProfileCompletenessCalculator
from src.core.cv_import import CVImportService

router = APIRouter(prefix="/profile", tags=["profile"])
templates = Jinja2Templates(directory="templates")

async def get_current_profile(db: AsyncSession):
    result = await db.execute(
        select(CandidateProfileExt).options(
            selectinload(CandidateProfileExt.sensitive_info),
            selectinload(CandidateProfileExt.addresses),
            selectinload(CandidateProfileExt.education),
            selectinload(CandidateProfileExt.employment),
            selectinload(CandidateProfileExt.documents),
            selectinload(CandidateProfileExt.compliance)
        ).limit(1)
    )
    return result.scalar_one_or_none()

@router.get("/", response_class=HTMLResponse)
async def view_profile_dashboard(request: Request, db: AsyncSession = Depends(get_db)):
    profile = await get_current_profile(db)
    scores = ProfileCompletenessCalculator.calculate(profile)
    return templates.TemplateResponse("profile.html", {"request": request, "scores": scores})

@router.get("/identity", response_class=HTMLResponse)
async def view_identity(request: Request, db: AsyncSession = Depends(get_db)):
    profile = await get_current_profile(db)
    masked_pan = "Not provided"
    masked_aadhaar = "Not provided"

    if profile and profile.sensitive_info:
        masked_pan = mask_sensitive(profile.sensitive_info.pan, 4) if profile.sensitive_info.pan else "Not provided"
        masked_aadhaar = mask_sensitive(profile.sensitive_info.aadhaar, 4) if profile.sensitive_info.aadhaar else "Not provided"

    return templates.TemplateResponse("profile_identity.html", {
        "request": request,
        "profile": profile,
        "masked_pan": masked_pan,
        "masked_aadhaar": masked_aadhaar
    })

@router.post("/identity")
async def update_identity(
    first_name: str = Form(...),
    last_name: str = Form(...),
    email: str = Form(...),
    pan: str = Form(""),
    aadhaar: str = Form(""),
    db: AsyncSession = Depends(get_db)
):
    profile = await get_current_profile(db)
    if not profile:
        profile = CandidateProfileExt(first_name=first_name, last_name=last_name, email=email)
        db.add(profile)
        await db.flush()
    else:
        profile.first_name = first_name
        profile.last_name = last_name
        profile.email = email

    if pan or aadhaar:
        if not profile.sensitive_info:
            profile.sensitive_info = SensitiveInfo(profile_id=profile.id)
            db.add(profile.sensitive_info)

        if pan: profile.sensitive_info.pan = pan
        if aadhaar: profile.sensitive_info.aadhaar = aadhaar

    await db.commit()
    return RedirectResponse(url="/profile/identity", status_code=303)

@router.get("/documents", response_class=HTMLResponse)
async def view_documents(request: Request, db: AsyncSession = Depends(get_db)):
    profile = await get_current_profile(db)
    documents = profile.documents if profile else []
    return templates.TemplateResponse("profile_documents.html", {"request": request, "documents": documents})

@router.post("/documents/upload")
async def upload_document(
    doc_type: str = Form(...),
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db)
):
    profile = await get_current_profile(db)
    if not profile:
        raise HTTPException(status_code=400, detail="Create identity first.")

    os.makedirs("vault", exist_ok=True)
    file_path = f"vault/{profile.id}_{file.filename}"

    content = await file.read()
    async with aiofiles.open(file_path, "wb") as f:
        await f.write(content)

    doc = Document(profile_id=profile.id, type=doc_type, file_path=file_path)
    db.add(doc)
    await db.commit()
    return RedirectResponse(url="/profile/documents", status_code=303)

@router.post("/import-cv")
async def import_cv(cv_file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    content = await cv_file.read()
    service = CVImportService()
    await service.import_cv(content.decode('utf-8', errors='ignore'), db)
    return RedirectResponse(url="/profile", status_code=303)
