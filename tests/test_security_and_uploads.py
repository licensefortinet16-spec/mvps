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


def test_entry_mutations_cannot_cross_tenants(client):
    from app.db import SessionLocal
    from app.models import FinancialEntry

    register(client, "Cliente Dono", "owner@test.local")
    response = client.post(
        "/entries/new",
        data={
            "title": "Despesa protegida",
            "category": "Outros",
            "entry_type": "expense",
            "amount": "42.00",
            "occurred_on": "2026-04-24",
            "notes": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = SessionLocal()
    try:
        entry = db.scalar(select(FinancialEntry).where(FinancialEntry.title == "Despesa protegida"))
        assert entry is not None
        entry_id = entry.id
    finally:
        db.close()

    logout(client)
    register(client, "Cliente Invasor", "intruder@test.local")

    edit_response = client.post(
        f"/entries/{entry_id}/edit",
        data={
            "title": "Alterada",
            "category": "Outros",
            "entry_type": "expense",
            "amount": "1.00",
            "occurred_on": "2026-04-24",
            "notes": "",
        },
        follow_redirects=False,
    )
    assert edit_response.status_code == 303
    assert edit_response.headers["location"] == "/entries"

    delete_response = client.post(f"/entries/{entry_id}/delete", follow_redirects=False)
    assert delete_response.status_code == 303

    db = SessionLocal()
    try:
        entry = db.get(FinancialEntry, entry_id)
        assert entry is not None
        assert entry.title == "Despesa protegida"
    finally:
        db.close()


def test_upload_rejects_invalid_files(client, monkeypatch):
    import app.routes.uploads as uploads_route
    from app.db import SessionLocal
    from app.models import Document

    monkeypatch.setattr(uploads_route, "process_document_async", lambda document_id: None)
    register(client, "Cliente Upload Seguro", "safe-upload@test.local")

    response = client.post(
        "/uploads",
        data={"document_type": "receipt"},
        files={"file": ("script.exe", BytesIO(b"bad"), "application/octet-stream")},
        follow_redirects=False,
    )
    assert response.status_code == 400

    response = client.post(
        "/uploads",
        data={"document_type": "receipt"},
        files={"file": ("vazio.txt", BytesIO(b""), "text/plain")},
        follow_redirects=False,
    )
    assert response.status_code == 400

    db = SessionLocal()
    try:
        assert db.scalar(select(Document)) is None
    finally:
        db.close()


def test_upload_sanitizes_filename(client, monkeypatch):
    import app.routes.uploads as uploads_route
    from app.db import SessionLocal
    from app.models import Document

    monkeypatch.setattr(uploads_route, "process_document_async", lambda document_id: None)
    register(client, "Cliente Nome Arquivo", "filename@test.local")

    response = client.post(
        "/uploads",
        data={"document_type": "receipt"},
        files={"file": ("../../cupom mercado abril.txt", BytesIO(b"valor total 10,00"), "text/plain")},
        follow_redirects=False,
    )
    assert response.status_code == 303

    db = SessionLocal()
    try:
        document = db.scalar(select(Document))
        assert document is not None
        assert document.filename == "cupom_mercado_abril.txt"
        assert ".." not in document.stored_path
    finally:
        db.close()


def test_document_review_retry_and_delete_cannot_cross_tenants(client, tmp_path):
    from app.db import SessionLocal
    from app.models import Document, DocumentType, User

    register(client, "Cliente Documento", "doc-owner@test.local")
    db = SessionLocal()
    try:
        owner = db.scalar(select(User).where(User.email == "doc-owner@test.local"))
        assert owner is not None
        stored_file = tmp_path / "documento.txt"
        stored_file.write_text("valor total 10,00", encoding="utf-8")
        document = Document(
            tenant_id=owner.tenant_id,
            user_id=owner.id,
            filename="documento.txt",
            stored_path=str(stored_file),
            content_hash="abc",
            file_size=10,
            content_type="text/plain",
            document_type=DocumentType.RECEIPT,
        )
        db.add(document)
        db.commit()
        db.refresh(document)
        document_id = document.id
    finally:
        db.close()

    logout(client)
    register(client, "Cliente Documento Invasor", "doc-intruder@test.local")

    review_response = client.get(f"/uploads/{document_id}/review", follow_redirects=False)
    assert review_response.status_code == 303
    assert review_response.headers["location"] == "/uploads"

    retry_response = client.post(f"/uploads/{document_id}/retry", follow_redirects=False)
    assert retry_response.status_code == 303
    assert retry_response.headers["location"] == "/uploads"

    delete_response = client.post(f"/uploads/{document_id}/delete", follow_redirects=False)
    assert delete_response.status_code == 303
    assert delete_response.headers["location"] == "/uploads"

    db = SessionLocal()
    try:
        assert db.get(Document, document_id) is not None
    finally:
        db.close()
