from __future__ import annotations

import uuid
from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import get_current_client
from app.models import Document, DocumentType, User
from app.services.audit import log_event
from app.services.documents import process_document, sync_payslip_outputs


router = APIRouter(prefix="/uploads")
settings = get_settings()


def _background_process(document_id: int) -> None:
    db = SessionLocal()
    try:
        process_document(db, document_id)
    finally:
        db.close()


@router.get("")
def uploads_page(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    documents = (
        db.execute(
            select(Document)
            .where(Document.tenant_id == user.tenant_id)
            .order_by(Document.created_at.desc())
        )
        .scalars()
        .all()
    )
    return request.app.state.templates.TemplateResponse("uploads.html", {"request": request, "documents": documents})


@router.post("")
async def create_upload(
    background_tasks: BackgroundTasks,
    document_type: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_client),
):
    tenant_dir = settings.upload_path / str(user.tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    target = tenant_dir / f"{uuid.uuid4().hex}_{file.filename}"
    content = await file.read()
    target.write_bytes(content)

    document = Document(
        tenant_id=user.tenant_id,
        user_id=user.id,
        filename=file.filename,
        stored_path=str(target),
        content_type=file.content_type,
        document_type=DocumentType(document_type),
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    log_event(db, "documents.uploaded", user=user, metadata={"document_id": document.id, "type": document_type})

    background_tasks.add_task(_background_process, document.id)
    return RedirectResponse("/uploads", status_code=303)


@router.get("/{document_id}/review")
def review_upload(document_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    document = db.get(Document, document_id)
    if not document or document.tenant_id != user.tenant_id:
        return RedirectResponse("/uploads", status_code=303)

    extracted_data = document.extracted_data or {}
    summary = extracted_data.get("summary") or {}
    items = extracted_data.get("items") or []
    if document.document_type != DocumentType.PAYSLIP:
        return RedirectResponse("/uploads", status_code=303)

    return request.app.state.templates.TemplateResponse(
        "upload_review.html",
        {
            "request": request,
            "document": document,
            "summary": summary,
            "items": items,
        },
    )


@router.post("/{document_id}/review")
async def save_review(document_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    document = db.get(Document, document_id)
    if not document or document.tenant_id != user.tenant_id or document.document_type != DocumentType.PAYSLIP:
        return RedirectResponse("/uploads", status_code=303)

    form = await request.form()
    summary = {
        "employee_name": (form.get("employee_name") or "").strip() or None,
        "company_name": (form.get("company_name") or "").strip() or None,
        "competence": (form.get("competence") or "").strip() or None,
        "gross_income": _parse_optional_float(form.get("gross_income")),
        "discounts": _parse_optional_float(form.get("discounts")),
        "net_income": _parse_optional_float(form.get("net_income")),
        "inss": _parse_optional_float(form.get("inss")),
        "irrf": _parse_optional_float(form.get("irrf")),
        "vt": _parse_optional_float(form.get("vt")),
        "vr": _parse_optional_float(form.get("vr")),
    }

    labels = form.getlist("item_label")
    amounts = form.getlist("item_amount")
    items = []
    for label, amount in zip(labels, amounts):
        clean_label = (label or "").strip()
        clean_amount = _parse_optional_float(amount)
        if clean_label and clean_amount and clean_amount > 0:
            items.append({"label": clean_label[:120], "amount": clean_amount})

    document.extracted_data = {
        "document_kind": "payslip",
        "filename": document.filename,
        "summary": summary,
        "items": items,
    }
    sync_payslip_outputs(db, document, document.extracted_data)
    log_event(db, "documents.reviewed", user=user, metadata={"document_id": document.id, "type": document.document_type.value})
    db.commit()
    return RedirectResponse("/uploads", status_code=303)


def _parse_optional_float(value: str | None) -> float | None:
    raw = (value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(Decimal(raw))
    except Exception:
        return None
