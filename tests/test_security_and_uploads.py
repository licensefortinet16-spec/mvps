from __future__ import annotations

from io import BytesIO

from sqlalchemy import select


def register(client, name: str, email: str, password: str = "12345678") -> None:
    response = client.post(
        "/register",
        data={"full_name": name, "email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def login(client, email: str, password: str = "12345678") -> None:
    response = client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=False,
    )
    assert response.status_code == 303


def logout(client) -> None:
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 303


def test_tenant_entries_are_isolated(client):
    register(client, "Cliente A", "a@test.local")
    response = client.post(
        "/entries/new",
        data={
            "title": "Despesa privada A",
            "category": "Outros",
            "entry_type": "expense",
            "amount": "99.90",
            "occurred_on": "2026-04-24",
            "notes": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    logout(client)

    register(client, "Cliente B", "b@test.local")
    response = client.get("/entries")
    assert response.status_code == 200
    assert "Despesa privada A" not in response.text


def test_role_permissions_redirect_to_allowed_area(client):
    register(client, "Cliente", "cliente@test.local")
    response = client.get("/admin", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    logout(client)

    login(client, "admin@test.local", "admin123456")
    response = client.get("/entries", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/admin"


def test_duplicate_upload_clicks_create_single_document(client, monkeypatch):
    import app.routes.uploads as uploads_route
    from app.db import SessionLocal
    from app.models import Document

    monkeypatch.setattr(uploads_route, "process_document_async", lambda document_id: None)
    register(client, "Cliente Upload", "upload@test.local")

    payload = b"valor total 10,00"
    for _ in range(2):
        response = client.post(
            "/uploads",
            data={"document_type": "receipt"},
            files={"file": ("cupom.txt", BytesIO(payload), "text/plain")},
            follow_redirects=False,
        )
        assert response.status_code == 303

    db = SessionLocal()
    try:
        documents = db.execute(select(Document)).scalars().all()
        assert len(documents) == 1
        assert documents[0].content_hash is not None
        assert documents[0].file_size == len(payload)
    finally:
        db.close()
