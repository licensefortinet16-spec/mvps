from __future__ import annotations

import importlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    db_path = tmp_path / "test.db"
    upload_path = tmp_path / "uploads"
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("UPLOAD_DIR", str(upload_path))
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("ADMIN_EMAIL", "admin@test.local")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin123456")
    monkeypatch.setenv("ADMIN_NAME", "Admin Test")
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "")
    monkeypatch.setenv("GROQ_API_KEY", "")

    import app.config
    import app.db
    import app.models
    import app.routes.admin
    import app.routes.auth
    import app.routes.categories
    import app.routes.dashboard
    import app.routes.entries
    import app.routes.uploads
    import app.main

    app.config.get_settings.cache_clear()
    importlib.reload(app.db)
    importlib.reload(app.models)
    importlib.reload(app.routes.admin)
    importlib.reload(app.routes.auth)
    importlib.reload(app.routes.categories)
    importlib.reload(app.routes.dashboard)
    importlib.reload(app.routes.entries)
    importlib.reload(app.routes.uploads)
    main = importlib.reload(app.main)

    with TestClient(main.app) as test_client:
        yield test_client
