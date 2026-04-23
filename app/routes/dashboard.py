from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_client
from app.models import Document, DocumentType, FinancialEntry, InstallmentPlan, User
from app.services.forecast import build_dashboard_snapshot
from app.services.recurring import generate_recurring_entries


router = APIRouter()


@router.get("/")
def home(request: Request, db: Session = Depends(get_db)):
    user_id = request.session.get("user_id")
    user_role = request.session.get("user_role")
    if not user_id:
        return request.app.state.templates.TemplateResponse(
            "landing.html",
            {
                "request": request,
                "google_enabled": bool(request.app.state.settings.google_client_id and request.app.state.settings.google_client_secret),
            },
        )
    if user_role == "admin":
        return RedirectResponse("/admin", status_code=303)
    user = db.get(User, user_id)
    if not user:
        request.session.clear()
        return RedirectResponse("/login", status_code=303)

    return render_client_dashboard(request, db, user)


@router.get("/dashboard")
def customer_dashboard(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    return render_client_dashboard(request, db, user)


def render_client_dashboard(request: Request, db: Session, user: User):
    generate_recurring_entries(db, user.tenant_id)
    snapshot = build_dashboard_snapshot(db, user.tenant_id)
    recent_entries = (
        db.execute(
            select(FinancialEntry)
            .where(FinancialEntry.tenant_id == user.tenant_id)
            .order_by(FinancialEntry.occurred_on.desc(), FinancialEntry.created_at.desc())
            .limit(8)
        )
        .scalars()
        .all()
    )
    recent_documents = (
        db.execute(
            select(Document)
            .where(Document.tenant_id == user.tenant_id)
            .order_by(Document.created_at.desc())
            .limit(6)
        )
        .scalars()
        .all()
    )
    plans = (
        db.execute(
            select(InstallmentPlan)
            .where(InstallmentPlan.tenant_id == user.tenant_id)
            .order_by(InstallmentPlan.created_at.desc())
            .limit(6)
        )
        .scalars()
        .all()
    )
    latest_payslip = db.execute(
        select(Document)
        .where(Document.tenant_id == user.tenant_id, Document.document_type == DocumentType.PAYSLIP)
        .order_by(Document.processed_at.desc(), Document.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    latest_payslip_summary = {}
    latest_payslip_items = []
    if latest_payslip and latest_payslip.extracted_data:
        latest_payslip_summary = latest_payslip.extracted_data.get("summary") or {}
        latest_payslip_items = latest_payslip.extracted_data.get("items") or []
    return request.app.state.templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "snapshot": snapshot,
            "recent_entries": recent_entries,
            "recent_documents": recent_documents,
            "plans": plans,
            "latest_payslip": latest_payslip,
            "latest_payslip_summary": latest_payslip_summary,
            "latest_payslip_items": latest_payslip_items,
        },
    )
