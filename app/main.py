from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import inspect, select, text
from starlette.middleware.sessions import SessionMiddleware

from app.auth import hash_password
from app.config import get_settings
from app.db import Base, SessionLocal, engine
from app.models import Tenant, User, UserRole
from app.routes import admin, auth, categories, dashboard, entries, uploads


settings = get_settings()
base_dir = Path(__file__).resolve().parent
static_dir = base_dir / "static"
templates_dir = base_dir / "templates"
app = FastAPI(title=settings.app_name)
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key,
    same_site="lax",
    https_only=settings.is_production,
)
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
app.state.templates = Jinja2Templates(directory=str(templates_dir))
app.state.settings = settings
app.state.templates.env.filters["brl"] = lambda value: f"{float(value):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


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


def ensure_plan_type_column() -> None:
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("installment_plans")}
    if "plan_type" in columns:
        return
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE installment_plans ADD COLUMN plan_type VARCHAR(20)"))
        connection.execute(
            text(
                "UPDATE installment_plans "
                "SET plan_type = CASE "
                "WHEN lower(category) LIKE '%financi%' THEN 'FINANCING' "
                "ELSE 'INSTALLMENT' END "
                "WHERE plan_type IS NULL"
            )
        )
        connection.execute(text("ALTER TABLE installment_plans ALTER COLUMN plan_type SET NOT NULL"))


def ensure_document_upload_metadata_columns() -> None:
    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("documents")}
    with engine.begin() as connection:
        if "content_hash" not in columns:
            connection.execute(text("ALTER TABLE documents ADD COLUMN content_hash VARCHAR(64)"))
            connection.execute(text("CREATE INDEX IF NOT EXISTS ix_documents_content_hash ON documents (content_hash)"))
        if "file_size" not in columns:
            connection.execute(text("ALTER TABLE documents ADD COLUMN file_size INTEGER"))


@app.on_event("startup")
def on_startup() -> None:
    settings.upload_path.mkdir(parents=True, exist_ok=True)
    Base.metadata.create_all(bind=engine)
    ensure_plan_type_column()
    ensure_document_upload_metadata_columns()
    bootstrap_admin()


app.include_router(auth.router)
app.include_router(dashboard.router)
app.include_router(entries.router)
app.include_router(uploads.router)
app.include_router(admin.router)
app.include_router(categories.router)
