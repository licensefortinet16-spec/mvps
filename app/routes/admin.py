from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_admin
from app.models import AuditEvent, Document, FinancialEntry, Tenant, User, UserRole


router = APIRouter(prefix="/admin")


@router.get("")
def admin_dashboard(request: Request, db: Session = Depends(get_db), admin: User = Depends(get_current_admin)):
    stats = {
        "tenants": db.scalar(select(func.count()).select_from(Tenant)) or 0,
        "users": db.scalar(select(func.count()).select_from(User).where(User.role == UserRole.USER)) or 0,
        "documents": db.scalar(select(func.count()).select_from(Document)) or 0,
        "entries": db.scalar(select(func.count()).select_from(FinancialEntry)) or 0,
        "events": db.scalar(select(func.count()).select_from(AuditEvent)) or 0,
    }
    document_status = (
        db.execute(select(Document.status, func.count()).group_by(Document.status).order_by(Document.status))
        .all()
    )
    return request.app.state.templates.TemplateResponse(
        "admin.html",
        {"request": request, "stats": stats, "document_status": document_status},
    )
