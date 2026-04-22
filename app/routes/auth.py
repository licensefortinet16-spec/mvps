from __future__ import annotations

from slugify import slugify
from authlib.integrations.starlette_client import OAuth
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import hash_password, verify_password
from app.config import get_settings
from app.db import get_db
from app.models import Tenant, User, UserRole
from app.services.audit import log_event


router = APIRouter()
settings = get_settings()
oauth = OAuth()

if settings.google_client_id and settings.google_client_secret:
    oauth.register(
        name="google",
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret,
        server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
        client_kwargs={"scope": "openid email profile"},
    )


def unique_slug(db: Session, base: str) -> str:
    slug = slugify(base) or "tenant"
    candidate = slug
    index = 1
    while db.scalar(select(Tenant).where(Tenant.slug == candidate)):
        index += 1
        candidate = f"{slug}-{index}"
    return candidate


def redirect_path_for_user(user: User) -> str:
    return "/admin" if user.role == UserRole.ADMIN else "/"


@router.get("/login")
def login_page(request: Request):
    user_role = request.session.get("user_role")
    if user_role == UserRole.ADMIN.value:
        return RedirectResponse("/admin", status_code=303)
    if user_role == UserRole.USER.value:
        return RedirectResponse("/", status_code=303)
    return request.app.state.templates.TemplateResponse(
        "login.html",
        {"request": request, "google_enabled": bool(settings.google_client_id and settings.google_client_secret)},
    )


@router.post("/login")
def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.email == email.lower().strip()))
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return request.app.state.templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "Credenciais invalidas",
                "google_enabled": bool(settings.google_client_id and settings.google_client_secret),
            },
            status_code=400,
        )

    request.session["user_id"] = user.id
    request.session["user_role"] = user.role.value
    log_event(db, "auth.login", user=user)
    return RedirectResponse(redirect_path_for_user(user), status_code=303)


@router.get("/register")
def register_page(request: Request):
    user_role = request.session.get("user_role")
    if user_role == UserRole.ADMIN.value:
        return RedirectResponse("/admin", status_code=303)
    if user_role == UserRole.USER.value:
        return RedirectResponse("/", status_code=303)
    return request.app.state.templates.TemplateResponse(
        "register.html",
        {"request": request, "google_enabled": bool(settings.google_client_id and settings.google_client_secret)},
    )


@router.post("/register")
def register(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    normalized_email = email.lower().strip()
    if db.scalar(select(User).where(User.email == normalized_email)):
        return request.app.state.templates.TemplateResponse(
            "register.html",
            {
                "request": request,
                "error": "E-mail ja cadastrado",
                "google_enabled": bool(settings.google_client_id and settings.google_client_secret),
            },
            status_code=400,
        )

    tenant = Tenant(name=full_name, slug=unique_slug(db, full_name))
    db.add(tenant)
    db.flush()
    user = User(
        tenant_id=tenant.id,
        full_name=full_name,
        email=normalized_email,
        password_hash=hash_password(password),
        role=UserRole.USER,
    )
    db.add(user)
    db.commit()
    request.session["user_id"] = user.id
    request.session["user_role"] = user.role.value
    log_event(db, "auth.register", user=user)
    return RedirectResponse(redirect_path_for_user(user), status_code=303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/", status_code=303)


@router.get("/login/google")
async def login_google(request: Request):
    if not (settings.google_client_id and settings.google_client_secret):
        return RedirectResponse("/login", status_code=303)
    redirect_uri = request.url_for("auth_google_callback")
    return await oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/google/callback", name="auth_google_callback")
async def auth_google_callback(request: Request, db: Session = Depends(get_db)):
    if not (settings.google_client_id and settings.google_client_secret):
        return RedirectResponse("/login", status_code=303)

    token = await oauth.google.authorize_access_token(request)
    user_info = token.get("userinfo")
    if not user_info:
        return RedirectResponse("/login", status_code=303)

    email = user_info["email"].lower().strip()
    google_sub = user_info["sub"]

    user = db.scalar(select(User).where(User.google_sub == google_sub))
    if not user:
        user = db.scalar(select(User).where(User.email == email))
        if user:
            user.google_sub = google_sub
        else:
            tenant = Tenant(name=user_info.get("name", email), slug=unique_slug(db, user_info.get("name", email)))
            db.add(tenant)
            db.flush()
            user = User(
                tenant_id=tenant.id,
                full_name=user_info.get("name", email),
                email=email,
                google_sub=google_sub,
                role=UserRole.USER,
            )
            db.add(user)
    db.commit()
    request.session["user_id"] = user.id
    request.session["user_role"] = user.role.value
    log_event(db, "auth.google_login", user=user)
    return RedirectResponse(redirect_path_for_user(user), status_code=303)
