from sqlalchemy.orm import Session

from app.models import AuditEvent, User


def log_event(db: Session, event_type: str, user: User | None = None, metadata: dict | None = None) -> None:
    event = AuditEvent(
        tenant_id=user.tenant_id if user else None,
        user_id=user.id if user else None,
        event_type=event_type,
        payload=metadata or {},
    )
    db.add(event)
    db.commit()
