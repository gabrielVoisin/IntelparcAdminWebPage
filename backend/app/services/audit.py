from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models import AuditLog


def audit(
    db: Session,
    action: str,
    entity: str,
    entity_id: object | None = None,
    actor_id: uuid.UUID | None = None,
    actor_kind: str = "staff",
    data: dict | None = None,
) -> None:
    """Append an entry to the audit log (who changed what, when)."""
    db.add(
        AuditLog(
            actor_id=actor_id,
            actor_kind=actor_kind,
            action=action,
            entity=entity,
            entity_id=str(entity_id) if entity_id is not None else None,
            data=data,
        )
    )
