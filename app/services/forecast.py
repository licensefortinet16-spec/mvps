from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import EntryType, FinancialEntry, Installment, InstallmentPlan, PayslipDeduction


def _month_key(value: date) -> str:
    return f"{value.year}-{value.month:02d}"


def build_dashboard_snapshot(db: Session, tenant_id: int) -> dict:
    entries = (
        db.execute(select(FinancialEntry).where(FinancialEntry.tenant_id == tenant_id).order_by(FinancialEntry.occurred_on))
        .scalars()
        .all()
    )
    installments = (
        db.execute(
            select(Installment, InstallmentPlan)
            .join(InstallmentPlan, Installment.plan_id == InstallmentPlan.id)
            .where(Installment.tenant_id == tenant_id)
            .order_by(Installment.due_date)
        )
        .all()
    )
    deductions = (
        db.execute(
            select(PayslipDeduction)
            .where(PayslipDeduction.tenant_id == tenant_id)
            .order_by(PayslipDeduction.occurred_on.desc(), PayslipDeduction.created_at.desc())
        )
        .scalars()
        .all()
    )

    monthly = defaultdict(lambda: {"income": Decimal("0"), "expense": Decimal("0")})
    categories = defaultdict(Decimal)
    totals = {"income": Decimal("0"), "expense": Decimal("0")}
    deduction_totals = defaultdict(Decimal)
    monthly_deductions = defaultdict(Decimal)

    for entry in entries:
        key = _month_key(entry.occurred_on)
        amount = Decimal(entry.amount)
        monthly[key][entry.entry_type.value] += amount
        totals[entry.entry_type.value] += amount
        if entry.entry_type == EntryType.EXPENSE:
            categories[entry.category] += amount

    for deduction in deductions:
        amount = Decimal(deduction.amount)
        deduction_totals[deduction.label] += amount
        monthly_deductions[_month_key(deduction.occurred_on)] += amount

    upcoming_installments = []
    for installment, plan in installments[:12]:
        upcoming_installments.append(
            {
                "title": plan.title,
                "sequence": installment.sequence,
                "amount": float(installment.amount),
                "due_date": installment.due_date.isoformat(),
                "status": installment.status.value,
            }
        )

    chart_months = sorted(monthly.keys())[-6:]
    monthly_chart = [
        {
            "month": month,
            "income": float(monthly[month]["income"]),
            "expense": float(monthly[month]["expense"]),
        }
        for month in chart_months
    ]
    category_chart = [{"category": key, "amount": float(value)} for key, value in sorted(categories.items(), key=lambda item: item[1], reverse=True)[:8]]
    deduction_chart = [{"label": key, "amount": float(value)} for key, value in sorted(deduction_totals.items(), key=lambda item: item[1], reverse=True)]
    recent_deductions = [
        {
            "label": deduction.label,
            "amount": float(deduction.amount),
            "competence": deduction.competence,
            "source": deduction.source.value,
        }
        for deduction in deductions[:8]
    ]
    deduction_total = float(sum(deduction_totals.values()))

    forecast = build_forecast(monthly_chart, upcoming_installments)

    return {
        "total_income": float(totals["income"]),
        "total_expense": float(totals["expense"]),
        "net": float(totals["income"] - totals["expense"]),
        "deduction_total": deduction_total,
        "monthly_chart": monthly_chart,
        "category_chart": category_chart,
        "deduction_chart": deduction_chart,
        "recent_deductions": recent_deductions,
        "upcoming_installments": upcoming_installments,
        "forecast": forecast,
    }


def build_forecast(monthly_chart: list[dict], upcoming_installments: list[dict]) -> list[dict]:
    if not monthly_chart:
        return []

    avg_income = sum(item["income"] for item in monthly_chart) / len(monthly_chart)
    avg_expense = sum(item["expense"] for item in monthly_chart) / len(monthly_chart)
    installments_by_month = defaultdict(float)
    for item in upcoming_installments:
        month = item["due_date"][:7]
        installments_by_month[month] += item["amount"]

    base_year, base_month = map(int, monthly_chart[-1]["month"].split("-"))
    forecast = []
    year = base_year
    month = base_month
    for _ in range(3):
        month += 1
        if month > 12:
            month = 1
            year += 1
        month_key = f"{year}-{month:02d}"
        expected_expense = avg_expense + installments_by_month.get(month_key, 0.0)
        forecast.append(
            {
                "month": month_key,
                "income": round(avg_income, 2),
                "expense": round(expected_expense, 2),
                "net": round(avg_income - expected_expense, 2),
            }
        )
    return forecast


def admin_usage_snapshot(db: Session) -> dict:
    users = db.scalar(select(func.count()).select_from(FinancialEntry))
    docs = db.scalar(select(func.count()).select_from(Installment))
    return {"entries": users or 0, "installments": docs or 0}
