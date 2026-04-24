from __future__ import annotations

import hashlib
import logging
import re
import threading
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import RedirectResponse
from pathlib import Path
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import SessionLocal, get_db
from app.deps import get_current_client
from app.models import Document, DocumentStatus, DocumentType, PayslipDeduction, User
from app.services.audit import log_event
from app.services.documents import process_document, sync_payslip_outputs, sync_spending_outputs, validate_spending_totals


router = APIRouter(prefix="/uploads")
settings = get_settings()
_upload_locks_guard = threading.Lock()
_upload_locks: set[tuple[int, str]] = set()
ALLOWED_UPLOAD_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".txt", ".csv"}
ALLOWED_UPLOAD_CONTENT_TYPES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "text/plain",
    "text/csv",
    "application/csv",
    "application/vnd.ms-excel",
}


def _clean_upload_filename(filename: str | None) -> str:
    raw_name = Path(filename or "documento").name.strip()
    stem = Path(raw_name).stem or "documento"
    suffix = Path(raw_name).suffix.lower()
    safe_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", stem).strip("._-") or "documento"
    return f"{safe_stem[:120]}{suffix}"


def _validate_upload(document_type: str, file: UploadFile, content: bytes) -> DocumentType:
    try:
        parsed_document_type = DocumentType(document_type)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tipo de documento invalido.") from exc

    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_UPLOAD_EXTENSIONS:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Formato de arquivo nao permitido.")

    content_type = (file.content_type or "").split(";")[0].strip().lower()
    if content_type and content_type not in ALLOWED_UPLOAD_CONTENT_TYPES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tipo de conteudo nao permitido.")

    if not content:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo vazio.")
    if len(content) > settings.max_upload_file_size_bytes:
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="Arquivo maior que o limite permitido.")

    return parsed_document_type


def process_document_async(document_id: int) -> None:
    db = SessionLocal()
    try:
        process_document(db, document_id)
    except Exception:
        logging.exception("Background document processing failed for document_id=%s", document_id)
    finally:
        db.close()


def _mark_stale_pending_as_failed(db: Session, tenant_id: int) -> None:
    """Documents stuck in PENDING for more than 10 minutes are considered failed."""
    cutoff = datetime.utcnow() - timedelta(minutes=10)
    stale = db.execute(
        select(Document).where(
            Document.tenant_id == tenant_id,
            Document.status == DocumentStatus.PENDING,
            Document.created_at < cutoff,
        )
    ).scalars().all()
    for doc in stale:
        doc.status = DocumentStatus.FAILED
        doc.extracted_data = {"error": "Processamento nao concluido. O arquivo pode ser inválido ou muito grande."}
        doc.processed_at = datetime.utcnow()
    if stale:
        db.commit()


@router.get("")
def uploads_page(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    _mark_stale_pending_as_failed(db, user.tenant_id)
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
    content = await file.read()
    parsed_document_type = _validate_upload(document_type, file, content)
    content_hash = hashlib.sha256(content).hexdigest()
    lock_key = (user.tenant_id, content_hash)
    with _upload_locks_guard:
        if lock_key in _upload_locks:
            return RedirectResponse("/uploads", status_code=303)
        _upload_locks.add(lock_key)

    try:
        return _create_upload_record(background_tasks, parsed_document_type, file, content, content_hash, db, user)
    finally:
        with _upload_locks_guard:
            _upload_locks.discard(lock_key)


def _create_upload_record(
    background_tasks: BackgroundTasks,
    document_type: DocumentType,
    file: UploadFile,
    content: bytes,
    content_hash: str,
    db: Session,
    user: User,
):
    duplicate_cutoff = datetime.utcnow() - timedelta(minutes=settings.upload_duplicate_window_minutes)
    duplicate = db.scalar(
        select(Document)
        .where(
            Document.tenant_id == user.tenant_id,
            Document.content_hash == content_hash,
            Document.created_at >= duplicate_cutoff,
        )
        .order_by(Document.created_at.desc())
    )
    if duplicate:
        return RedirectResponse("/uploads", status_code=303)

    tenant_dir = settings.upload_path / str(user.tenant_id)
    tenant_dir.mkdir(parents=True, exist_ok=True)
    clean_filename = _clean_upload_filename(file.filename)
    target = tenant_dir / f"{uuid.uuid4().hex}_{clean_filename}"
    target.write_bytes(content)

    document = Document(
        tenant_id=user.tenant_id,
        user_id=user.id,
        filename=clean_filename,
        stored_path=str(target),
        content_hash=content_hash,
        file_size=len(content),
        content_type=file.content_type,
        document_type=document_type,
    )
    db.add(document)
    db.commit()
    db.refresh(document)
    log_event(db, "documents.uploaded", user=user, metadata={"document_id": document.id, "type": document_type.value})
    background_tasks.add_task(process_document_async, document.id)
    return RedirectResponse("/uploads", status_code=303)


@router.get("/{document_id}/review")
def review_upload(document_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    document = db.get(Document, document_id)
    if not document or document.tenant_id != user.tenant_id:
        return RedirectResponse("/uploads", status_code=303)

    extracted_data = document.extracted_data or {}
    summary = extracted_data.get("summary") or {}
    items = extracted_data.get("items") or []
    return request.app.state.templates.TemplateResponse(
        "upload_review.html",
        {
            "request": request,
            "document": document,
            "summary": summary,
            "items": items,
            "warnings": extracted_data.get("warnings") or [],
        },
    )


@router.post("/{document_id}/review")
async def save_review(document_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    document = db.get(Document, document_id)
    if not document or document.tenant_id != user.tenant_id:
        return RedirectResponse("/uploads", status_code=303)

    form = await request.form()
    if document.document_type != DocumentType.PAYSLIP:
        merchant = (form.get("merchant") or document.filename).strip()[:160]
        occurred_on = (form.get("occurred_on") or "").strip() or None
        detected_total = _parse_optional_float(form.get("detected_total"))
        labels = form.getlist("item_label")
        gross_amounts = form.getlist("item_gross_amount")
        discount_amounts = form.getlist("item_discount_amount")
        amounts = form.getlist("item_amount")
        items = []
        for index, (label, amount) in enumerate(zip(labels, amounts)):
            clean_label = (label or "").strip()
            gross_amount = _parse_optional_float(gross_amounts[index]) if index < len(gross_amounts) else None
            discount_amount = _parse_optional_float(discount_amounts[index]) if index < len(discount_amounts) else None
            clean_amount = _parse_optional_float(amount)
            if clean_label and clean_amount and clean_amount > 0:
                items.append(
                    {
                        "label": clean_label[:120],
                        "gross_amount": gross_amount,
                        "discount_amount": discount_amount,
                        "net_amount": clean_amount,
                        "amount": clean_amount,
                    }
                )
        extracted_data = validate_spending_totals({
            "summary": {
                "merchant": merchant,
                "detected_total": detected_total,
                "occurred_on": occurred_on,
                "document_kind": document.document_type.value,
            },
            "items": items,
        })
        if (extracted_data.get("summary") or {}).get("has_total_mismatch"):
            document.extracted_data = extracted_data
            document.confidence = min(document.confidence, 0.49)
            db.commit()
            return request.app.state.templates.TemplateResponse(
                "upload_review.html",
                {
                    "request": request,
                    "document": document,
                    "summary": extracted_data.get("summary") or {},
                    "items": extracted_data.get("items") or [],
                    "warnings": extracted_data.get("warnings") or [],
                    "error": "A soma dos itens nao fecha com o total detectado. Corrija o total, os descontos ou os valores finais antes de consolidar.",
                },
                status_code=400,
            )
        document.extracted_data = extracted_data
        document.confidence = max(document.confidence, 0.9)
        sync_spending_outputs(db, document, document.extracted_data)
        log_event(db, "documents.reviewed", user=user, metadata={"document_id": document.id, "type": document.document_type.value})
        db.commit()
        return RedirectResponse("/uploads", status_code=303)

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


@router.post("/{document_id}/retry")
def retry_upload(document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    document = db.get(Document, document_id)
    if not document or document.tenant_id != user.tenant_id:
        return RedirectResponse("/uploads", status_code=303)
    if not Path(document.stored_path).exists():
        document.status = DocumentStatus.FAILED
        document.extracted_data = {"error": "Arquivo nao encontrado no servidor. Faca o upload novamente."}
        document.processed_at = datetime.utcnow()
        db.commit()
        return RedirectResponse("/uploads", status_code=303)
    document.status = DocumentStatus.PENDING
    document.extracted_data = None
    document.extracted_text = None
    document.confidence = 0.0
    document.processed_at = None
    db.commit()
    process_document(db, document.id)
    log_event(db, "documents.retry", user=user, metadata={"document_id": document.id})
    return RedirectResponse("/uploads", status_code=303)


@router.post("/{document_id}/delete")
def delete_document(document_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    document = db.get(Document, document_id)
    if not document or document.tenant_id != user.tenant_id:
        return RedirectResponse("/uploads", status_code=303)
    file_path = Path(document.stored_path)
    if file_path.exists():
        file_path.unlink(missing_ok=True)
    # Remove dependent rows before deleting the document
    db.execute(delete(PayslipDeduction).where(PayslipDeduction.document_id == document_id))
    db.delete(document)
    db.commit()
    log_event(db, "documents.delete", user=user, metadata={"document_id": document_id})
    return RedirectResponse("/uploads", status_code=303)


def _parse_optional_float(value: str | None) -> float | None:
    raw = (value or "").strip().replace(",", ".")
    if not raw:
        return None
    try:
        return float(Decimal(raw))
    except Exception:
        return None
