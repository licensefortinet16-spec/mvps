from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

from app.auth import hash_password
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import Tenant, User, UserRole
from app.routes import admin, auth, dashboard, entries, uploads


settings = get_settings()
app = FastAPI(title=settings.app_name)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.is_production,
)
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.state.templates = Jinja2Templates(directory="app/templates")
app.state.settings = settings


@app.middleware("http")
async def load_current_user(request, call_next):
    request.state.current_user = None
    session = request.scope.get("session") or {}
    user_id = session.get("user_id")
    if user_id:
        db = SessionLocal()
        try:
            request.state.current_user = db.get(User, user_id)
        finally:
            db.close()
    response = await call_next(request)
    return response


def bootstrap_admin() -> None:
    db = SessionLocal()
    try:
        admin_user = db.scalar(select(User).where(User.email == settings.admin_email.lower().strip()))
        if admin_user:
            return
        tenant = Tenant(name="Administracao", slug="admin")
        db.add(tenant)
        db.flush()
        user = User(
            tenant_id=tenant.id,
            full_name=settings.admin_name,
            email=settings.admin_email.lower().strip(),
            password_hash=hash_password(settings.admin_password),
            role=UserRole.ADMIN,
        )
        db.add(user)
        db.commit()
    finally:
        db.close()


@app.on_event("startup")
def on_startup() -> None:
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    bootstrap_admin()


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(entries.router)
app.include_router(uploads.router)
app.include_router(admin.router)
