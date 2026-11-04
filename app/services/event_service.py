"""Event creation, de-duplication, review workflow and reporting queries."""

from __future__ import annotations

import logging
import uuid
from datetime import date, datetime
from typing import Optional, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Employee, EquipmentAsset, Event, User
from app.models.enums import (
    BUSINESS_EVENT_TYPES,
    EQUIPMENT_EVENT_TYPES,
    PERSON_EVENT_TYPES,
    POSSESSION_EVENT_TYPES,
    Direction,
    EventType,
    SyncStatus,
    VerificationStatus,
)
from app.services import audit_service, inventory_service
from app.utils.time_utils import cooldown_bucket, local_day_bounds_utc, utc_now

logger = logging.getLogger(__name__)

MOVEMENT_EVENT_TYPES = set(EQUIPMENT_EVENT_TYPES)


def new_public_id() -> str:
    return f"EVT-{uuid.uuid4().hex[:10].upper()}"


def build_dedupe_key(
    *,
    event_type: EventType | str,
    camera_code: str,
    occurred_at: datetime,
    cooldown_seconds: int,
    person_track_id: Optional[int] = None,
    object_track_id: Optional[int] = None,
) -> str:
    """Stable key that prevents the same physical movement being logged twice.

    Track ids are temporary identifiers, so they are combined with a cooldown
    bucket: within one bucket the same track can only produce one event of the
    same type on the same camera.
    """
    bucket = cooldown_bucket(occurred_at, cooldown_seconds)
    if EventType(str(event_type)) in POSSESSION_EVENT_TYPES:
        # Pick-up/put-down describe one (object, person) pair; two different
        # objects handled in the same second must not cancel each other out.
        return f"pos:{camera_code}:{event_type}:t{object_track_id or 'x'}:p{person_track_id or 'x'}:{bucket}"
    if EventType(str(event_type)) in EQUIPMENT_EVENT_TYPES:
        return f"mv:{camera_code}:{event_type}:t{object_track_id or 'x'}:p{person_track_id or 'x'}:{bucket}"
    if EventType(str(event_type)) in PERSON_EVENT_TYPES:
        return f"pp:{camera_code}:{event_type}:t{person_track_id or 'x'}:{bucket}"
    return f"sys:{camera_code}:{event_type}:{bucket}"


def create_event(
    session: Session,
    *,
    event_type: EventType | str,
    camera_code: str,
    occurred_at: Optional[datetime] = None,
    employee_id: Optional[int] = None,
    person_track_id: Optional[int] = None,
    asset_id: Optional[int] = None,
    equipment_category: Optional[str] = None,
    object_track_id: Optional[int] = None,
    direction: Direction | str = Direction.NONE,
    detection_confidence: Optional[float] = None,
    face_match_score: Optional[float] = None,
    association_confidence: Optional[float] = None,
    verification_status: VerificationStatus | str = VerificationStatus.PENDING,
    evidence_path: Optional[str] = None,
    notes: Optional[str] = None,
    extra: Optional[dict] = None,
    dedupe_key: Optional[str] = None,
    cooldown_seconds: Optional[int] = None,
    create_inventory_candidate: bool = True,
) -> tuple[Event, bool]:
    """Insert an event, returning ``(event, created)``.

    Duplicate suppression happens twice: a pre-check on ``dedupe_key`` and the
    unique constraint itself (in case two workers hit the same instant).
    """
    settings = get_settings()
    moment = occurred_at or utc_now()
    event_type_value = EventType(str(event_type))
    cooldown = cooldown_seconds if cooldown_seconds is not None else settings.event_cooldown_seconds
    key = dedupe_key or build_dedupe_key(
        event_type=event_type_value,
        camera_code=camera_code,
        occurred_at=moment,
        cooldown_seconds=cooldown,
        person_track_id=person_track_id,
        object_track_id=object_track_id,
    )

    existing = session.scalar(select(Event).where(Event.dedupe_key == key))
    if existing is not None:
        logger.debug("Duplicate event ignored: %s", key)
        return existing, False

    event = Event(
        public_id=new_public_id(),
        event_type=event_type_value.value,
        occurred_at=moment,
        camera_code=camera_code,
        employee_id=employee_id,
        person_track_id=person_track_id,
        asset_id=asset_id,
        equipment_category=equipment_category,
        object_track_id=object_track_id,
        direction=Direction(str(direction)).value,
        detection_confidence=detection_confidence,
        face_match_score=face_match_score,
        association_confidence=association_confidence,
        verification_status=VerificationStatus(str(verification_status)).value,
        evidence_path=evidence_path,
        sheets_sync_status=SyncStatus.PENDING.value,
        notes=notes,
        extra=extra or {},
        dedupe_key=key,
    )

    try:
        with session.begin_nested():
            session.add(event)
            session.flush()
            if create_inventory_candidate and event_type_value in EQUIPMENT_EVENT_TYPES:
                inventory_service.create_pending_movement(session, event)
    except IntegrityError:
        duplicate = session.scalar(select(Event).where(Event.dedupe_key == key))
        if duplicate is not None:
            logger.info("Concurrent duplicate suppressed: %s", key)
            return duplicate, False
        raise

    logger.info(
        "Event %s %s camera=%s status=%s",
        event.public_id,
        event.event_type,
        camera_code,
        event.verification_status,
    )
    return event, True


def review_event(
    session: Session,
    event: Event,
    *,
    decision: VerificationStatus | str,
    user: Optional[User] = None,
    reason: str = "",
    employee_id: Optional[int] = None,
    asset_id: Optional[int] = None,
    equipment_category: Optional[str] = None,
) -> Event:
    """Confirm or reject a pending event, updating the ledger only on confirm."""
    target = VerificationStatus(str(decision))
    if target not in {VerificationStatus.CONFIRMED, VerificationStatus.REJECTED}:
        raise ValueError("Faqat 'CONFIRMED' yoki 'REJECTED' qarorini qabul qilish mumkin.")
    if event.verification_status != VerificationStatus.PENDING.value:
        raise ValueError("Bu hodisa allaqachon ko‘rib chiqilgan.")

    if employee_id is not None:
        employee = session.get(Employee, employee_id)
        if employee is None:
            raise ValueError("Tanlangan xodim topilmadi.")
        event.employee_id = employee.id
    if asset_id is not None:
        asset = session.get(EquipmentAsset, asset_id)
        if asset is None:
            # A stale id from the form would otherwise fail on the FK at flush time.
            raise ValueError("Tanlangan jihoz topilmadi.")
        event.asset_id = asset.id
    if equipment_category:
        event.equipment_category = equipment_category

    if target is VerificationStatus.CONFIRMED:
        inventory_service.confirm_event(session, event, user=user)
        event.verification_status = VerificationStatus.CONFIRMED.value
        event.notes = (event.notes or "") + (f"\nTasdiqlandi: {reason}" if reason else "")
    else:
        if not reason.strip():
            raise ValueError("Rad etish uchun sabab kiritilishi shart — «Izoh / sabab» maydonini to‘ldiring.")
        inventory_service.reject_event(session, event, reason=reason, user=user)
        event.verification_status = VerificationStatus.REJECTED.value
        event.notes = (event.notes or "") + f"\nRad etildi: {reason}"

    event.reviewed_at = utc_now()
    event.reviewed_by_user_id = user.id if user else None
    event.review_reason = reason or None
    # The row changed, so it must be re-synchronised to Google Sheets.
    event.sheets_sync_status = SyncStatus.PENDING.value
    session.flush()

    audit_service.record(
        session,
        action=f"event.{target.value.lower()}",
        entity="event",
        entity_id=event.public_id,
        details={
            "event_type": event.event_type,
            "employee_id": event.employee_id,
            "equipment_category": event.equipment_category,
            "asset_id": event.asset_id,
        },
        reason=reason or None,
        user=user,
        username="operator",
    )
    return event


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------
def _apply_filters(
    stmt,
    *,
    event_types: Optional[Sequence[EventType | str]] = None,
    status: Optional[VerificationStatus | str] = None,
    camera_code: Optional[str] = None,
    employee_id: Optional[int] = None,
    category: Optional[str] = None,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
    search: Optional[str] = None,
):
    if event_types:
        stmt = stmt.where(Event.event_type.in_([str(t) for t in event_types]))
    if status:
        stmt = stmt.where(Event.verification_status == str(status))
    if camera_code:
        stmt = stmt.where(Event.camera_code == camera_code)
    if employee_id:
        stmt = stmt.where(Event.employee_id == employee_id)
    if category:
        stmt = stmt.where(Event.equipment_category == category)
    if start:
        stmt = stmt.where(Event.occurred_at >= start)
    if end:
        stmt = stmt.where(Event.occurred_at <= end)
    if search:
        like = f"%{search.strip()}%"
        stmt = stmt.where(
            (Event.public_id.ilike(like))
            | (Event.camera_code.ilike(like))
            | (Event.notes.ilike(like))
        )
    return stmt


def query_events(
    session: Session,
    *,
    limit: int = 50,
    offset: int = 0,
    **filters,
) -> list[Event]:
    stmt = _apply_filters(select(Event), **filters)
    stmt = stmt.order_by(Event.occurred_at.desc(), Event.id.desc()).offset(offset).limit(limit)
    return list(session.scalars(stmt).all())


def count_events(session: Session, **filters) -> int:
    stmt = _apply_filters(select(func.count(Event.id)), **filters)
    return int(session.scalar(stmt) or 0)


def get_event(session: Session, event_id: int) -> Optional[Event]:
    return session.get(Event, event_id)


def pending_reviews(session: Session, limit: int = 100) -> list[Event]:
    return query_events(session, status=VerificationStatus.PENDING, limit=limit)


def count_pending(session: Session) -> int:
    return count_events(session, status=VerificationStatus.PENDING)


def pending_sync_events(session: Session, limit: int = 500) -> list[Event]:
    stmt = (
        select(Event)
        .where(Event.sheets_sync_status.in_([SyncStatus.PENDING.value, SyncStatus.FAILED.value]))
        .order_by(Event.occurred_at.asc())
        .limit(limit)
    )
    return list(session.scalars(stmt).all())


def needs_review_flag(event: Event) -> bool:
    """Heuristic used by the dashboard badge."""
    if event.verification_status == VerificationStatus.PENDING.value:
        return True
    if event.association_confidence is not None and event.association_confidence < 0.5:
        return True
    return False


# ---------------------------------------------------------------------------
# Aggregations for the dashboard
# ---------------------------------------------------------------------------
def dashboard_stats(session: Session, day: Optional[date] = None) -> dict:
    from app.utils.time_utils import local_today

    day = day or local_today()
    start, end = local_day_bounds_utc(day)

    def _count(types: Sequence[EventType]) -> int:
        return count_events(session, event_types=types, start=start, end=end)

    equipment_in = _count([EventType.EQUIPMENT_BROUGHT_IN])
    equipment_out = _count([EventType.EQUIPMENT_TAKEN_OUT])
    return {
        "day": day,
        "entries": _count([EventType.PERSON_ENTERED, EventType.UNKNOWN_PERSON_ENTERED]),
        "exits": _count([EventType.PERSON_EXITED, EventType.UNKNOWN_PERSON_EXITED]),
        "unknown_persons": _count([EventType.UNKNOWN_PERSON_DETECTED]),
        "equipment_in": equipment_in,
        "equipment_out": equipment_out,
        "pending_reviews": count_pending(session),
        "camera_events": _count([EventType.CAMERA_DISCONNECTED, EventType.CAMERA_RECONNECTED]),
    }


def recent_events_for_camera(session: Session, camera_code: str, limit: int = 10) -> list[Event]:
    return query_events(session, camera_code=camera_code, limit=limit)
