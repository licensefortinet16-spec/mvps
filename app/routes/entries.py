from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_client
from app.models import DeductionSource, EntryType, FinancialEntry, Installment, InstallmentPlan, PayslipDeduction, User
from app.services.audit import log_event


router = APIRouter(prefix="/entries")


@router.get("/new")
def new_entry(request: Request, user: User = Depends(get_current_client)):
    return request.app.state.templates.TemplateResponse("entry_form.html", {"request": request})


@router.post("/new")
def create_entry(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    entry_type: str = Form(...),
    amount: float = Form(...),
    occurred_on: date = Form(...),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_client),
):
    entry = FinancialEntry(
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=title,
        category=category,
        entry_type=EntryType(entry_type),
        amount=amount,
        occurred_on=occurred_on,
        notes=notes or None,
        source="manual",
    )
    db.add(entry)
    db.commit()
    log_event(db, "entries.create", user=user, metadata={"entry_type": entry_type, "amount": amount})
    return RedirectResponse("/", status_code=303)


@router.get("/plans/new")
def new_plan(request: Request, user: User = Depends(get_current_client)):
    return request.app.state.templates.TemplateResponse("installment_form.html", {"request": request})


@router.post("/plans/new")
def create_plan(
    request: Request,
    title: str = Form(...),
    merchant: str = Form(""),
    category: str = Form(...),
    total_amount: float = Form(...),
    installment_count: int = Form(...),
    start_date: date = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_client),
):
    plan = InstallmentPlan(
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=title,
        merchant=merchant or None,
        category=category,
        total_amount=total_amount,
        installment_count=installment_count,
        start_date=start_date,
        source="manual",
    )
    db.add(plan)
    db.flush()
    installment_amount = (Decimal(str(total_amount)) / Decimal(str(installment_count))).quantize(Decimal("0.01"))
    for index in range(installment_count):
        db.add(
            Installment(
                tenant_id=user.tenant_id,
                plan_id=plan.id,
                sequence=index + 1,
                due_date=start_date + timedelta(days=30 * index),
                amount=installment_amount,
            )
        )
    db.commit()
    log_event(db, "installments.create_plan", user=user, metadata={"plan_id": plan.id, "count": installment_count})
    return RedirectResponse("/", status_code=303)


@router.get("/deductions/new")
def new_deduction(request: Request, user: User = Depends(get_current_client)):
    return request.app.state.templates.TemplateResponse("deduction_form.html", {"request": request})


@router.post("/deductions/new")
def create_deduction(
    request: Request,
    label: str = Form(...),
    amount: float = Form(...),
    competence: str = Form(""),
    occurred_on: date = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_client),
):
    deduction = PayslipDeduction(
        tenant_id=user.tenant_id,
        user_id=user.id,
        document_id=None,
        label=label,
        amount=amount,
        competence=competence or None,
        occurred_on=occurred_on,
        source=DeductionSource.MANUAL,
    )
    db.add(deduction)
    db.commit()
    log_event(db, "deductions.create", user=user, metadata={"label": label, "amount": amount})
    return RedirectResponse("/", status_code=303)


@router.get("")
def list_entries(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    entries = (
        db.execute(
            select(FinancialEntry)
            .where(FinancialEntry.tenant_id == user.tenant_id)
            .order_by(FinancialEntry.occurred_on.desc(), FinancialEntry.created_at.desc())
        )
        .scalars()
        .all()
    )
    return request.app.state.templates.TemplateResponse("entries_list.html", {"request": request, "entries": entries})
