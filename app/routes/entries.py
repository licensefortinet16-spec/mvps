from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
import re

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_client
from app.models import (
    DeductionSource, EntryType, FinancialEntry, Installment, InstallmentPlan,
    PayslipDeduction, PlanType, RecurringExpense, RecurringFrequency, User, UserCategory,
)
from app.services.audit import log_event


router = APIRouter(prefix="/entries")


def _get_categories(db: Session, tenant_id: int) -> list[str]:
    from app.models import DEFAULT_CATEGORIES_EXPENSE, DEFAULT_CATEGORIES_INCOME
    custom = db.execute(
        select(UserCategory.name).where(UserCategory.tenant_id == tenant_id).order_by(UserCategory.name)
    ).scalars().all()
    all_cats = sorted(set(DEFAULT_CATEGORIES_EXPENSE + DEFAULT_CATEGORIES_INCOME + list(custom)))
    return all_cats


@router.get("/new")
def new_entry(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    return request.app.state.templates.TemplateResponse(
        "entry_form.html",
        {"request": request, "entry": None, "mode": "create", "categories": _get_categories(db, user.tenant_id)},
    )


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
    return RedirectResponse("/entries", status_code=303)


@router.get("/{entry_id}/edit")
def edit_entry_page(entry_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    entry = db.get(FinancialEntry, entry_id)
    if not entry or entry.tenant_id != user.tenant_id:
        return RedirectResponse("/entries", status_code=303)
    return request.app.state.templates.TemplateResponse(
        "entry_form.html",
        {"request": request, "entry": entry, "mode": "edit", "categories": _get_categories(db, user.tenant_id)},
    )


@router.post("/{entry_id}/edit")
def update_entry(
    entry_id: int,
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
    entry = db.get(FinancialEntry, entry_id)
    if not entry or entry.tenant_id != user.tenant_id:
        return RedirectResponse("/entries", status_code=303)
    entry.title = title
    entry.category = category
    entry.entry_type = EntryType(entry_type)
    entry.amount = amount
    entry.occurred_on = occurred_on
    entry.notes = notes or None
    db.commit()
    log_event(db, "entries.update", user=user, metadata={"entry_id": entry_id})
    return RedirectResponse("/entries", status_code=303)


@router.post("/{entry_id}/delete")
def delete_entry(entry_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    entry = db.get(FinancialEntry, entry_id)
    if not entry or entry.tenant_id != user.tenant_id:
        return RedirectResponse("/entries", status_code=303)
    db.delete(entry)
    db.commit()
    log_event(db, "entries.delete", user=user, metadata={"entry_id": entry_id})
    return RedirectResponse("/entries", status_code=303)


@router.get("/plans/new")
def new_plan(request: Request, user: User = Depends(get_current_client)):
    return request.app.state.templates.TemplateResponse(
        "installment_form.html",
        {"request": request, "plan": None, "mode": "create"},
    )


@router.post("/plans/new")
def create_plan(
    request: Request,
    title: str = Form(...),
    merchant: str = Form(""),
    plan_type: str = Form(...),
    category: str = Form(...),
    total_amount: str = Form(""),
    installment_amount: str = Form(""),
    installment_count: int = Form(...),
    start_date: date = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_client),
):
    resolved_total_amount, resolved_installment_amount, error = resolve_plan_amounts(
        total_amount=total_amount,
        installment_amount=installment_amount,
        installment_count=installment_count,
    )
    if error:
        return request.app.state.templates.TemplateResponse(
            "installment_form.html",
            {
                "request": request,
                "plan": None,
                "mode": "create",
                "error": error,
                "form_values": {
                    "title": title,
                    "merchant": merchant,
                    "plan_type": plan_type,
                    "category": category,
                    "total_amount": total_amount,
                    "installment_amount": installment_amount,
                    "installment_count": installment_count,
                    "start_date": start_date.isoformat(),
                },
            },
            status_code=400,
        )

    plan = InstallmentPlan(
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=title,
        merchant=merchant or None,
        plan_type=PlanType(plan_type),
        category=category,
        total_amount=float(resolved_total_amount),
        installment_count=installment_count,
        start_date=start_date,
        source="manual",
    )
    db.add(plan)
    db.flush()
    replace_plan_installments(
        db=db,
        tenant_id=user.tenant_id,
        plan_id=plan.id,
        installment_count=installment_count,
        installment_amount=resolved_installment_amount,
        start_date=start_date,
    )
    db.commit()
    log_event(db, "installments.create_plan", user=user, metadata={"plan_id": plan.id, "count": installment_count})
    return RedirectResponse("/entries/plans", status_code=303)


@router.get("/plans")
def list_plans(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    plans = (
        db.execute(
            select(InstallmentPlan)
            .where(InstallmentPlan.tenant_id == user.tenant_id)
            .order_by(InstallmentPlan.created_at.desc())
        )
        .scalars()
        .all()
    )
    today = date.today()
    items = []
    for plan in plans:
        paid_count = sum(1 for installment in plan.installments if installment.status.value == "paid")
        next_installment = next((installment for installment in plan.installments if installment.due_date >= today), None)
        installment_amount = float(plan.installments[0].amount) if plan.installments else 0.0
        items.append(
            {
                "plan": plan,
                "paid_count": paid_count,
                "remaining_count": max(plan.installment_count - paid_count, 0),
                "installment_amount": installment_amount,
                "next_installment": next_installment,
                "plan_type": plan.plan_type.value,
            }
        )
    return request.app.state.templates.TemplateResponse(
        "plans_list.html",
        {"request": request, "plans": items},
    )


@router.get("/plans/{plan_id}/edit")
def edit_plan_page(plan_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    plan = db.get(InstallmentPlan, plan_id)
    if not plan or plan.tenant_id != user.tenant_id:
        return RedirectResponse("/entries/plans", status_code=303)
    return request.app.state.templates.TemplateResponse(
        "installment_form.html",
        {"request": request, "plan": plan, "mode": "edit"},
    )


@router.post("/plans/{plan_id}/edit")
def update_plan(
    plan_id: int,
    request: Request,
    title: str = Form(...),
    merchant: str = Form(""),
    plan_type: str = Form(...),
    category: str = Form(...),
    total_amount: str = Form(""),
    installment_amount: str = Form(""),
    installment_count: int = Form(...),
    start_date: date = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_client),
):
    plan = db.get(InstallmentPlan, plan_id)
    if not plan or plan.tenant_id != user.tenant_id:
        return RedirectResponse("/entries/plans", status_code=303)

    resolved_total_amount, resolved_installment_amount, error = resolve_plan_amounts(
        total_amount=total_amount,
        installment_amount=installment_amount,
        installment_count=installment_count,
    )
    if error:
        return request.app.state.templates.TemplateResponse(
            "installment_form.html",
            {
                "request": request,
                "plan": plan,
                "mode": "edit",
                "error": error,
                "form_values": {
                    "title": title,
                    "merchant": merchant,
                    "plan_type": plan_type,
                    "category": category,
                    "total_amount": total_amount,
                    "installment_amount": installment_amount,
                    "installment_count": installment_count,
                    "start_date": start_date.isoformat(),
                },
            },
            status_code=400,
        )

    plan.title = title
    plan.merchant = merchant or None
    plan.plan_type = PlanType(plan_type)
    plan.category = category
    plan.total_amount = float(resolved_total_amount)
    plan.installment_count = installment_count
    plan.start_date = start_date
    replace_plan_installments(
        db=db,
        tenant_id=user.tenant_id,
        plan_id=plan.id,
        installment_count=installment_count,
        installment_amount=resolved_installment_amount,
        start_date=start_date,
    )
    db.commit()
    log_event(db, "installments.update_plan", user=user, metadata={"plan_id": plan.id, "count": installment_count})
    return RedirectResponse("/entries/plans", status_code=303)


@router.post("/plans/{plan_id}/delete")
def delete_plan(plan_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    plan = db.get(InstallmentPlan, plan_id)
    if not plan or plan.tenant_id != user.tenant_id:
        return RedirectResponse("/entries/plans", status_code=303)
    db.delete(plan)
    db.commit()
    log_event(db, "installments.delete_plan", user=user, metadata={"plan_id": plan_id})
    return RedirectResponse("/entries/plans", status_code=303)


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


@router.get("/recurring")
def list_recurring(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    recurrings = (
        db.execute(
            select(RecurringExpense)
            .where(RecurringExpense.tenant_id == user.tenant_id)
            .order_by(RecurringExpense.is_active.desc(), RecurringExpense.created_at.desc())
        )
        .scalars()
        .all()
    )
    return request.app.state.templates.TemplateResponse(
        "recurring_list.html",
        {"request": request, "recurrings": recurrings},
    )


@router.get("/recurring/new")
def new_recurring(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    return request.app.state.templates.TemplateResponse(
        "recurring_form.html",
        {"request": request, "recurring": None, "mode": "create", "categories": _get_categories(db, user.tenant_id)},
    )


@router.post("/recurring/new")
def create_recurring(
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    entry_type: str = Form(...),
    amount: float = Form(...),
    frequency: str = Form(...),
    start_date: date = Form(...),
    end_date: str = Form(""),
    notes: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_client),
):
    parsed_end_date: date | None = None
    if end_date.strip():
        try:
            from datetime import datetime as _dt
            parsed_end_date = _dt.strptime(end_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
    recurring = RecurringExpense(
        tenant_id=user.tenant_id,
        user_id=user.id,
        title=title,
        category=category,
        entry_type=EntryType(entry_type),
        amount=amount,
        frequency=RecurringFrequency(frequency),
        start_date=start_date,
        end_date=parsed_end_date,
        notes=notes or None,
    )
    db.add(recurring)
    db.commit()
    log_event(db, "recurring.create", user=user, metadata={"title": title, "frequency": frequency})
    return RedirectResponse("/entries/recurring", status_code=303)


@router.get("/recurring/{recurring_id}/edit")
def edit_recurring_page(recurring_id: int, request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    recurring = db.get(RecurringExpense, recurring_id)
    if not recurring or recurring.tenant_id != user.tenant_id:
        return RedirectResponse("/entries/recurring", status_code=303)
    return request.app.state.templates.TemplateResponse(
        "recurring_form.html",
        {"request": request, "recurring": recurring, "mode": "edit", "categories": _get_categories(db, user.tenant_id)},
    )


@router.post("/recurring/{recurring_id}/edit")
def update_recurring(
    recurring_id: int,
    request: Request,
    title: str = Form(...),
    category: str = Form(...),
    entry_type: str = Form(...),
    amount: float = Form(...),
    frequency: str = Form(...),
    start_date: date = Form(...),
    end_date: str = Form(""),
    notes: str = Form(""),
    is_active: str = Form("on"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_client),
):
    recurring = db.get(RecurringExpense, recurring_id)
    if not recurring or recurring.tenant_id != user.tenant_id:
        return RedirectResponse("/entries/recurring", status_code=303)
    parsed_end_date: date | None = None
    if end_date.strip():
        try:
            from datetime import datetime as _dt
            parsed_end_date = _dt.strptime(end_date.strip(), "%Y-%m-%d").date()
        except ValueError:
            pass
    recurring.title = title
    recurring.category = category
    recurring.entry_type = EntryType(entry_type)
    recurring.amount = amount
    recurring.frequency = RecurringFrequency(frequency)
    recurring.start_date = start_date
    recurring.end_date = parsed_end_date
    recurring.notes = notes or None
    recurring.is_active = is_active == "on"
    db.commit()
    log_event(db, "recurring.update", user=user, metadata={"recurring_id": recurring_id})
    return RedirectResponse("/entries/recurring", status_code=303)


@router.post("/recurring/{recurring_id}/delete")
def delete_recurring(recurring_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    recurring = db.get(RecurringExpense, recurring_id)
    if recurring and recurring.tenant_id == user.tenant_id:
        db.delete(recurring)
        db.commit()
        log_event(db, "recurring.delete", user=user, metadata={"recurring_id": recurring_id})
    return RedirectResponse("/entries/recurring", status_code=303)


@router.get("")
def list_entries(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    import re as _re
    all_entries = (
        db.execute(
            select(FinancialEntry)
            .where(FinancialEntry.tenant_id == user.tenant_id)
            .order_by(FinancialEntry.occurred_on.desc(), FinancialEntry.created_at.desc())
        )
        .scalars()
        .all()
    )

    # Separate upload-grouped entries (have "document_id=N" in notes) from standalone
    _doc_id_re = _re.compile(r"document_id=(\d+)")
    groups: dict[int, dict] = {}
    standalone: list = []

    for entry in all_entries:
        m = _doc_id_re.search(entry.notes or "")
        if entry.source == "upload" and m:
            doc_id = int(m.group(1))
            if doc_id not in groups:
                # Extract merchant name from notes: "Importado de MERCHANT (document_id=N)"
                merchant_match = _re.match(r"Importado de (.+?) \(document_id=", entry.notes or "")
                merchant = merchant_match.group(1) if merchant_match else entry.title
                groups[doc_id] = {
                    "doc_id": doc_id,
                    "merchant": merchant,
                    "occurred_on": entry.occurred_on,
                    "category": entry.category,
                    "entry_type": entry.entry_type,
                    "total": 0.0,
                    "items": [],
                }
            groups[doc_id]["total"] = round(groups[doc_id]["total"] + float(entry.amount), 2)
            groups[doc_id]["items"].append(entry)
        else:
            standalone.append(entry)

    # Build display list: each element is either a standalone entry or a group dict
    # Interleave by occurred_on descending
    display: list = []
    gi = sorted(groups.values(), key=lambda g: g["occurred_on"], reverse=True)
    si = list(standalone)

    gi_idx = si_idx = 0
    while gi_idx < len(gi) or si_idx < len(si):
        g_date = gi[gi_idx]["occurred_on"] if gi_idx < len(gi) else None
        s_date = si[si_idx].occurred_on if si_idx < len(si) else None
        if g_date and (s_date is None or g_date >= s_date):
            display.append(("group", gi[gi_idx]))
            gi_idx += 1
        else:
            display.append(("entry", si[si_idx]))
            si_idx += 1

    return request.app.state.templates.TemplateResponse(
        "entries_list.html", {"request": request, "display": display}
    )


def resolve_plan_amounts(total_amount: str, installment_amount: str, installment_count: int) -> tuple[Decimal, Decimal, str | None]:
    if installment_count < 2:
        return Decimal("0"), Decimal("0"), "A quantidade de parcelas precisa ser pelo menos 2."

    total = parse_currency_input(total_amount)
    per_installment = parse_currency_input(installment_amount)

    if total <= 0 and per_installment <= 0:
        return Decimal("0"), Decimal("0"), "Informe o valor total do contrato ou o valor da parcela."

    if total > 0 and per_installment > 0:
        expected_total = (per_installment * Decimal(installment_count)).quantize(Decimal("0.01"))
        if expected_total != total:
            return Decimal("0"), Decimal("0"), "O valor total nao bate com a parcela multiplicada pela quantidade."
        return total, per_installment, None

    if per_installment > 0:
        total = (per_installment * Decimal(installment_count)).quantize(Decimal("0.01"))
        return total, per_installment, None

    per_installment = (total / Decimal(installment_count)).quantize(Decimal("0.01"))
    return total, per_installment, None


def parse_currency_input(raw_value: str | float | int | None) -> Decimal:
    if raw_value is None:
        return Decimal("0.00")
    if isinstance(raw_value, (int, float)):
        return Decimal(str(raw_value)).quantize(Decimal("0.01"))

    value = str(raw_value).strip()
    if not value:
        return Decimal("0.00")

    sanitized = re.sub(r"[^\d,.-]", "", value)
    if not sanitized:
        return Decimal("0.00")

    if "," in sanitized:
        normalized = sanitized.replace(".", "").replace(",", ".")
    elif sanitized.count(".") > 1:
        normalized = sanitized.replace(".", "")
    else:
        normalized = sanitized

    return Decimal(normalized).quantize(Decimal("0.01"))


def replace_plan_installments(
    db: Session,
    tenant_id: int,
    plan_id: int,
    installment_count: int,
    installment_amount: Decimal,
    start_date: date,
) -> None:
    db.execute(delete(Installment).where(Installment.plan_id == plan_id))
    for index in range(installment_count):
        db.add(
            Installment(
                tenant_id=tenant_id,
                plan_id=plan_id,
                sequence=index + 1,
                due_date=start_date + timedelta(days=30 * index),
                amount=installment_amount,
            )
        )
