"""Audit trail for administrator actions."""

from __future__ import annotations

from typing import Any, Optional

from sqlalchemy.orm import Session

from app.models import AuditLog, User
from app.utils.time_utils import utc_now


def record(
    session: Session,
    *,
    action: str,
    entity: str = "",
    entity_id: Any = "",
    details: Optional[dict] = None,
    reason: Optional[str] = None,
    user: Optional[User] = None,
    username: Optional[str] = None,
) -> AuditLog:
    """Append an audit row. Never raises for missing user information."""
    entry = AuditLog(
        occurred_at=utc_now(),
        user_id=user.id if user else None,
        username=username or (user.username if user else "system"),
        action=action,
        entity=entity,
        entity_id=str(entity_id or ""),
        details=details or {},
        reason=reason,
    )
    session.add(entry)
    session.flush()
    return entry


def recent(session: Session, limit: int = 50) -> list[AuditLog]:
    from sqlalchemy import select

    return list(
        session.scalars(select(AuditLog).order_by(AuditLog.occurred_at.desc()).limit(limit)).all()
    )
