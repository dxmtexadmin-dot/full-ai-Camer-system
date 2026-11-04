"""Event creation: de-duplication, review workflow, reporting queries."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import func, select

from app.models import Event
from app.models.enums import EventType, SyncStatus, VerificationStatus
from app.services import event_service, inventory_service


def test_duplicate_event_in_same_cooldown_bucket_is_ignored(session):
    # Fixed instant inside a 30 second bucket, so the assertion is deterministic.
    moment = datetime(2026, 1, 1, 10, 0, 5, tzinfo=timezone.utc)
    first, created_first = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        occurred_at=moment,
        equipment_category="monitor",
        object_track_id=5,
        person_track_id=2,
        cooldown_seconds=30,
    )
    second, created_second = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        occurred_at=moment + timedelta(seconds=3),
        equipment_category="monitor",
        object_track_id=5,
        person_track_id=2,
        cooldown_seconds=30,
    )
    session.commit()

    assert created_first is True
    assert created_second is False
    assert first.id == second.id
    assert session.scalar(select(func.count(Event.id))) == 1
    # Only one ledger candidate as well.
    assert len(inventory_service.event_transactions(session, first.id)) == 1


def test_event_after_cooldown_bucket_is_new(session):
    moment = datetime(2026, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
    event_service.create_event(
        session,
        event_type=EventType.PERSON_ENTERED,
        camera_code="CAM-01",
        occurred_at=moment,
        person_track_id=2,
        cooldown_seconds=5,
    )
    _, created = event_service.create_event(
        session,
        event_type=EventType.PERSON_ENTERED,
        camera_code="CAM-01",
        occurred_at=moment + timedelta(seconds=30),
        person_track_id=2,
        cooldown_seconds=5,
    )
    session.commit()
    assert created is True
    assert session.scalar(select(func.count(Event.id))) == 2


def test_different_cameras_do_not_collide(session):
    moment = datetime(2026, 2, 1, 8, 0, 0, tzinfo=timezone.utc)
    event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        occurred_at=moment,
        equipment_category="laptop",
        object_track_id=1,
        person_track_id=1,
    )
    _, created = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-02",
        occurred_at=moment,
        equipment_category="laptop",
        object_track_id=1,
        person_track_id=1,
    )
    session.commit()
    assert created is True


def test_person_events_have_no_inventory_candidate(session):
    event, _ = event_service.create_event(
        session, event_type=EventType.PERSON_ENTERED, camera_code="CAM-01", person_track_id=9
    )
    session.commit()
    assert inventory_service.event_transactions(session, event.id) == []


def test_public_ids_are_unique(session):
    ids = set()
    for _ in range(20):
        ids.add(event_service.new_public_id())
    assert len(ids) == 20
    assert all(value.startswith("EVT-") and len(value) == 14 for value in ids)


def test_event_requires_started_pending_state(session):
    event, _ = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        equipment_category="monitor",
        object_track_id=1,
        person_track_id=1,
    )
    session.commit()
    assert event.verification_status == VerificationStatus.PENDING.value
    assert event.sheets_sync_status == SyncStatus.PENDING.value


def test_review_confirms_and_updates_ledger(session, admin_user):
    inventory_service.record(
        session, category_code="monitor", transaction_type="INITIAL", quantity=5, user=admin_user
    )
    event, _ = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        equipment_category="monitor",
        object_track_id=1,
        person_track_id=1,
    )
    session.commit()

    event_service.review_event(
        session, event, decision=VerificationStatus.CONFIRMED, user=admin_user, reason="Tekshirildi"
    )
    session.commit()

    assert event.verification_status == VerificationStatus.CONFIRMED.value
    assert event.reviewed_at is not None
    assert event.reviewed_by_user_id == admin_user.id
    assert inventory_service.balance_for(session, "monitor").expected == 4


def test_review_rejection_requires_reason(session, admin_user):
    event, _ = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        equipment_category="monitor",
        object_track_id=1,
        person_track_id=1,
    )
    session.commit()

    with pytest.raises(ValueError, match="sabab"):
        event_service.review_event(session, event, decision=VerificationStatus.REJECTED, user=admin_user, reason="  ")


def test_event_can_only_be_reviewed_once(session, admin_user):
    event, _ = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        equipment_category="monitor",
        object_track_id=1,
        person_track_id=1,
    )
    session.commit()
    event_service.review_event(
        session, event, decision=VerificationStatus.REJECTED, user=admin_user, reason="Xato aniqlash"
    )
    session.commit()

    with pytest.raises(ValueError, match="ko‘rib chiqilgan"):
        event_service.review_event(
            session, event, decision=VerificationStatus.CONFIRMED, user=admin_user
        )


def test_rejection_marks_event_for_resync(session, admin_user):
    event, _ = event_service.create_event(
        session,
        event_type=EventType.EQUIPMENT_TAKEN_OUT,
        camera_code="CAM-01",
        equipment_category="monitor",
        object_track_id=1,
        person_track_id=1,
    )
    event.sheets_sync_status = SyncStatus.SYNCED.value
    session.commit()

    event_service.review_event(
        session, event, decision=VerificationStatus.REJECTED, user=admin_user, reason="Sinov rad etish"
    )
    session.commit()
    assert event.sheets_sync_status == SyncStatus.PENDING.value


def test_counts_and_pending_queries(session):
    event_service.create_event(
        session, event_type=EventType.EQUIPMENT_TAKEN_OUT, camera_code="CAM-01",
        equipment_category="monitor", object_track_id=1, person_track_id=1,
    )
    event_service.create_event(
        session, event_type=EventType.PERSON_ENTERED, camera_code="CAM-01", person_track_id=2,
    )
    session.commit()

    assert event_service.count_pending(session) == 2
    assert len(event_service.pending_reviews(session)) == 2
    assert event_service.count_events(session, camera_code="CAM-01") == 2
    assert event_service.count_events(session, camera_code="CAM-99") == 0

    stats = event_service.dashboard_stats(session)
    assert stats["entries"] == 1
    assert stats["equipment_out"] == 1
    assert stats["pending_reviews"] == 2


def test_pending_sync_events_includes_failed(session):
    event, _ = event_service.create_event(
        session, event_type=EventType.PERSON_ENTERED, camera_code="CAM-01", person_track_id=1
    )
    session.commit()
    assert [item.id for item in event_service.pending_sync_events(session)] == [event.id]

    event.sheets_sync_status = SyncStatus.SYNCED.value
    session.commit()
    assert event_service.pending_sync_events(session) == []
