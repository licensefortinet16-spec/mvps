from __future__ import annotations

import calendar
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import EntryType, FinancialEntry, RecurringExpense, RecurringFrequency


def _next_due_date(frequency: RecurringFrequency, from_date: date) -> date:
    if frequency == RecurringFrequency.WEEKLY:
        return from_date + timedelta(days=7)
    if frequency == RecurringFrequency.BIWEEKLY:
        return from_date + timedelta(days=14)
    if frequency == RecurringFrequency.MONTHLY:
        month = from_date.month + 1
        year = from_date.year
        if month > 12:
            month = 1
            year += 1
        day = min(from_date.day, calendar.monthrange(year, month)[1])
        return from_date.replace(year=year, month=month, day=day)
    # YEARLY
    year = from_date.year + 1
    day = min(from_date.day, calendar.monthrange(year, from_date.month)[1])
    return from_date.replace(year=year, day=day)


def generate_recurring_entries(db: Session, tenant_id: int) -> int:
    today = date.today()
    recurrings = (
        db.execute(
            select(RecurringExpense).where(
                RecurringExpense.tenant_id == tenant_id,
                RecurringExpense.is_active == True,  # noqa: E712
            )
        )
        .scalars()
        .all()
    )

    count = 0
    for recurring in recurrings:
        if recurring.last_generated is None:
            check_date = recurring.start_date
        else:
            check_date = _next_due_date(recurring.frequency, recurring.last_generated)

        while check_date <= today:
            if recurring.end_date and check_date > recurring.end_date:
                break
            db.add(
                FinancialEntry(
                    tenant_id=recurring.tenant_id,
                    user_id=recurring.user_id,
                    title=recurring.title,
                    category=recurring.category,
                    entry_type=recurring.entry_type,
                    amount=float(recurring.amount),
                    occurred_on=check_date,
                    source="recurring",
                    notes=recurring.notes,
                )
            )
            recurring.last_generated = check_date
            count += 1
            check_date = _next_due_date(recurring.frequency, check_date)

    if count:
        db.commit()
    return count
