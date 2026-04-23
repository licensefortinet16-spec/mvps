from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_client
from app.models import DEFAULT_CATEGORIES_EXPENSE, DEFAULT_CATEGORIES_INCOME, User, UserCategory
from app.services.audit import log_event


router = APIRouter(prefix="/categories")


@router.get("")
def list_categories(request: Request, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    categories = (
        db.execute(
            select(UserCategory)
            .where(UserCategory.tenant_id == user.tenant_id)
            .order_by(UserCategory.entry_type, UserCategory.name)
        )
        .scalars()
        .all()
    )
    return request.app.state.templates.TemplateResponse(
        "categories.html",
        {
            "request": request,
            "categories": categories,
            "defaults_expense": DEFAULT_CATEGORIES_EXPENSE,
            "defaults_income": DEFAULT_CATEGORIES_INCOME,
        },
    )


@router.post("/new")
def create_category(
    request: Request,
    name: str = Form(...),
    entry_type: str = Form("both"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_client),
):
    name = name.strip()[:80]
    if not name:
        return RedirectResponse("/categories", status_code=303)
    existing = db.scalar(
        select(UserCategory).where(
            UserCategory.tenant_id == user.tenant_id,
            UserCategory.name == name,
        )
    )
    if not existing:
        db.add(UserCategory(tenant_id=user.tenant_id, user_id=user.id, name=name, entry_type=entry_type))
        db.commit()
        log_event(db, "categories.create", user=user, metadata={"name": name})
    return RedirectResponse("/categories", status_code=303)


@router.post("/{category_id}/delete")
def delete_category(category_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_client)):
    category = db.get(UserCategory, category_id)
    if category and category.tenant_id == user.tenant_id:
        db.delete(category)
        db.commit()
        log_event(db, "categories.delete", user=user, metadata={"category_id": category_id})
    return RedirectResponse("/categories", status_code=303)
