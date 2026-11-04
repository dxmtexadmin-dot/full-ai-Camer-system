"""Admin-only data maintenance: delete stills and non-authoritative events.

The inventory ledger is append-only: nothing here touches
``inventory_transactions``. Only *observation* rows (events) and the evidence
JPEGs on disk can be removed, and only when they are not part of the audit
trail (a confirmed event, or an event the ledger points at).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import PROJECT_ROOT, Settings, get_settings
from app.models import Event, InventoryTransaction, User
from app.models.enums import VerificationStatus
from app.services import audit_service, evidence_service
from app.utils.time_utils import utc_now

logger = logging.getLogger(__name__)


class CleanupError(ValueError):
    """A destructive action that would break the audit trail."""


def _unlink(relative: Optional[str], settings: Optional[Settings] = None) -> bool:
    """Delete one evidence file; never raises for a missing or foreign path."""
    if not relative:
        return False
    try:
        path = evidence_service.resolve_evidence(relative, settings)
    except ValueError:
        logger.warning("Dalil yo‘li ruxsat etilgan papkadan tashqarida: %s", relative)
        return False
    try:
        if path.exists():
            path.unlink()
            return True
    except OSError as exc:  # pragma: no cover - permissions
        logger.warning("Dalil faylini o‘chirib bo‘lmadi (%s): %s", path, exc)
    return False


def _linked_event_ids(session: Session) -> set[int]:
    rows = session.scalars(
        select(InventoryTransaction.event_id).where(InventoryTransaction.event_id.is_not(None))
    ).all()
    return {int(item) for item in rows if item is not None}


def _check_deletable(event: Event, linked: set[int]) -> None:
    if event is None:
        raise CleanupError("Hodisa topilmadi.")
    if event.verification_status == VerificationStatus.CONFIRMED.value:
        raise CleanupError(
            f"{event.public_id} tasdiqlangan hodisa — o‘chirib bo‘lmaydi (jurnal izi saqlanadi)."
        )
    if event.id in linked:
        raise CleanupError(
            f"{event.public_id} inventar jurnaliga bog‘langan — avval uni rad eting."
        )


def _event_details(event: Event) -> dict:
    return {
        "event_type": event.event_type,
        "camera_code": event.camera_code,
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "equipment_category": event.equipment_category,
        "employee_id": event.employee_id,
        "verification_status": event.verification_status,
    }


def delete_evidence(
    session: Session,
    event: Event,
    *,
    user: Optional[User] = None,
    settings: Optional[Settings] = None,
) -> bool:
    """Remove the still of one event; the event row itself stays."""
    relative = event.evidence_path
    removed = _unlink(relative, settings)
    if not relative:
        return False
    event.evidence_path = None
    event.notes = (event.notes or "") + "\nDalil rasmi administrator tomonidan o‘chirildi."
    audit_service.record(
        session,
        action="event.evidence.deleted",
        entity="event",
        entity_id=event.public_id,
        details={"file": relative, "removed": removed, **_event_details(event)},
        user=user,
    )
    session.flush()
    logger.info("Dalil rasmi o‘chirildi: %s (%s)", event.public_id, relative)
    return removed


def delete_event(
    session: Session,
    event: Event,
    *,
    user: Optional[User] = None,
    reason: str = "",
    settings: Optional[Settings] = None,
) -> str:
    """Delete one event (and its still). Returns the public id."""
    _check_deletable(event, _linked_event_ids(session))
    public_id = event.public_id
    details = _event_details(event)
    had_evidence = _unlink(event.evidence_path, settings)
    audit_service.record(
        session,
        action="event.deleted",
        entity="event",
        entity_id=public_id,
        details={**details, "evidence_removed": had_evidence},
        reason=reason or None,
        user=user,
    )
    session.delete(event)
    session.flush()
    logger.info("Hodisa o‘chirildi: %s (%s)", public_id, details["event_type"])
    return public_id


def purge_events(
    session: Session,
    *,
    event_types: Optional[Sequence[str]] = None,
    statuses: Optional[Sequence[str]] = None,
    before: Optional[datetime] = None,
    user: Optional[User] = None,
    reason: str = "",
    settings: Optional[Settings] = None,
) -> dict:
    """Bulk-delete junk events; confirmed and ledger-linked rows are skipped."""
    stmt = select(Event)
    if event_types:
        stmt = stmt.where(Event.event_type.in_([str(item) for item in event_types]))
    if statuses:
        stmt = stmt.where(Event.verification_status.in_([str(item) for item in statuses]))
    if before is not None:
        stmt = stmt.where(Event.occurred_at < before)

    linked = _linked_event_ids(session)
    removed = 0
    skipped = 0
    files = 0
    for event in list(session.scalars(stmt).all()):
        if event.verification_status == VerificationStatus.CONFIRMED.value or event.id in linked:
            skipped += 1
            continue
        if _unlink(event.evidence_path, settings):
            files += 1
        session.delete(event)
        removed += 1
    session.flush()
    if removed:
        audit_service.record(
            session,
            action="event.purged",
            entity="event",
            entity_id="bulk",
            details={
                "removed": removed,
                "skipped": skipped,
                "evidence_removed": files,
                "event_types": [str(item) for item in (event_types or [])],
                "statuses": [str(item) for item in (statuses or [])],
                "before": before.isoformat() if before else None,
            },
            reason=reason or None,
            user=user,
        )
        logger.info(
            "Ommaviy tozalash: %d hodisa o‘chirildi, %d himoyalangan, %d rasm",
            removed,
            skipped,
            files,
        )
    return {"removed": removed, "skipped": skipped, "files": files}


def evidence_files(settings: Optional[Settings] = None) -> list[Path]:
    settings = settings or get_settings()
    if not settings.evidence_dir.exists():
        return []
    return list(settings.evidence_dir.rglob("*.jpg"))


def purge_evidence(
    session: Session,
    *,
    days: int,
    user: Optional[User] = None,
    settings: Optional[Settings] = None,
) -> int:
    """Delete stills older than ``days`` and detach them from their events."""
    settings = settings or get_settings()
    cutoff = utc_now() - timedelta(days=max(1, int(days)))
    removed = 0
    for path in evidence_files(settings):
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        except OSError:  # pragma: no cover - raced with retention janitor
            continue
        if modified >= cutoff:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError as exc:  # pragma: no cover - permissions
            logger.warning("Dalil faylini o‘chirib bo‘lmadi (%s): %s", path, exc)

    detached = 0
    for event in session.scalars(select(Event).where(Event.evidence_path.is_not(None))).all():
        try:
            target = evidence_service.resolve_evidence(event.evidence_path, settings)
        except ValueError:
            event.evidence_path = None
            detached += 1
            continue
        if not target.exists():
            event.evidence_path = None
            detached += 1
    if removed or detached:
        audit_service.record(
            session,
            action="evidence.purged",
            entity="evidence",
            entity_id="bulk",
            details={"removed": removed, "detached": detached, "older_than_days": int(days)},
            user=user,
        )
    session.flush()
    logger.info("Eski dalillar tozalandi: %d fayl, %d hodisadan uzildi", removed, detached)
    return removed


def orphan_evidence(session: Session, settings: Optional[Settings] = None) -> list[Path]:
    """Files on disk that no event refers to any more."""
    settings = settings or get_settings()
    known = {
        Path(item).name
        for (item,) in session.execute(select(Event.evidence_path).where(Event.evidence_path.is_not(None))).all()
        if item
    }
    return [path for path in evidence_files(settings) if path.name not in known]


def purge_orphan_evidence(
    session: Session,
    *,
    user: Optional[User] = None,
    settings: Optional[Settings] = None,
) -> int:
    removed = 0
    for path in orphan_evidence(session, settings):
        try:
            path.unlink()
            removed += 1
        except OSError as exc:  # pragma: no cover - permissions
            logger.warning("Yetim dalilni o‘chirib bo‘lmadi (%s): %s", path, exc)
    if removed:
        audit_service.record(
            session,
            action="evidence.orphans.purged",
            entity="evidence",
            entity_id="bulk",
            details={"removed": removed},
            user=user,
        )
    session.flush()
    return removed


def _sqlite_path(settings: Settings) -> Optional[Path]:
    url = settings.database_url
    if not url.startswith("sqlite"):
        return None
    raw = url.split("///", 1)[-1]
    if not raw or raw == ":memory:":
        return None
    path = Path(raw)
    return path if path.is_absolute() else PROJECT_ROOT / path


def storage_stats(session: Session, settings: Optional[Settings] = None) -> dict:
    """What is on disk and in the database right now (for the /data page)."""
    settings = settings or get_settings()
    files = evidence_files(settings)
    evidence_bytes = 0
    for path in files:
        try:
            evidence_bytes += path.stat().st_size
        except OSError:  # pragma: no cover
            continue
    by_status = {
        str(status): int(count)
        for status, count in session.execute(
            select(Event.verification_status, func.count(Event.id)).group_by(Event.verification_status)
        ).all()
    }
    db_path = _sqlite_path(settings)
    db_bytes = db_path.stat().st_size if db_path is not None and db_path.exists() else 0
    return {
        "events_total": int(session.scalar(select(func.count(Event.id))) or 0),
        "by_status": by_status,
        "evidence_files": len(files),
        "evidence_bytes": evidence_bytes,
        "orphan_files": len(orphan_evidence(session, settings)),
        "db_bytes": db_bytes,
        "retention_days": settings.evidence_retention_days,
        "evidence_dir": str(settings.evidence_dir),
    }


def human_bytes(value: int) -> str:
    size = float(max(0, int(value)))
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"
