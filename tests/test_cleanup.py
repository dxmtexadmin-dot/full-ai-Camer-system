"""Admin-only deletion of evidence stills and junk events.

The point of the feature is that an operator can clean up false detections and
old stills without ever touching the append-only inventory ledger.
"""

from __future__ import annotations

import re
from datetime import timedelta
from pathlib import Path

import pytest

from app.models import Event
from app.models.enums import EventType, VerificationStatus
from app.services import cleanup_service, event_service
from app.utils.time_utils import utc_now


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found"
    return match.group(1)


def login(client, username: str = "admin", password: str = "Admin123!") -> None:
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "next": "/", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def fake_evidence(settings, public_id: str) -> str:
    relative = Path("2026-10-29") / f"{public_id}.jpg"
    target = settings.evidence_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"\xff\xd8\xff\xd9")
    return relative.as_posix()


def make_event(session, *, event_type=EventType.CAMERA_DISCONNECTED, days_ago: int = 0, **kwargs):
    event, _ = event_service.create_event(
        session,
        event_type=event_type,
        camera_code="CAM",
        occurred_at=utc_now() - timedelta(days=days_ago),
        **kwargs,
    )
    session.commit()
    return event


# -- single event deletion ----------------------------------------------------
def test_admin_deletes_an_event_and_its_still(client, session, settings):
    event = make_event(session, equipment_category="smartphone", object_track_id=4)
    event.evidence_path = fake_evidence(settings, event.public_id)
    session.commit()
    public_id = event.public_id
    event_id = event.id

    login(client)
    page = client.get("/review")
    token = csrf_from(page.text)
    response = client.post(
        f"/api/events/{event_id}/delete",
        data={"csrf_token": token, "next_url": "/review"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    assert session.get(Event, event_id) is None
    assert not (settings.evidence_dir / "2026-10-29" / f"{public_id}.jpg").exists()


def test_deleting_only_the_still_keeps_the_event(client, session, settings):
    event = make_event(session)
    event.evidence_path = fake_evidence(settings, event.public_id)
    session.commit()

    login(client)
    token = csrf_from(client.get("/review").text)
    response = client.post(
        f"/api/events/{event.id}/evidence/delete",
        data={"csrf_token": token, "next_url": "/review"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    kept = session.get(Event, event.id)
    assert kept is not None
    assert kept.evidence_path is None
    assert "o‘chirildi" in (kept.notes or "")


def test_confirmed_event_cannot_be_deleted(client, session):
    event = make_event(session)
    event.verification_status = VerificationStatus.CONFIRMED.value
    session.commit()

    login(client)
    token = csrf_from(client.get("/review").text)
    response = client.post(
        f"/api/events/{event.id}/delete",
        data={"csrf_token": token, "next_url": "/review"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert session.get(Event, event.id) is not None


def test_operator_cannot_delete_anything(client, session, other_user_factory):
    event = make_event(session)
    other_user_factory("op", "operator")

    login(client, "op", "Operator123!")
    token = csrf_from(client.get("/review").text)
    response = client.post(
        f"/api/events/{event.id}/delete",
        data={"csrf_token": token, "next_url": "/review"},
        follow_redirects=False,
    )

    assert response.status_code == 403
    assert session.get(Event, event.id) is not None


# -- bulk cleanup -------------------------------------------------------------
def test_bulk_purge_removes_old_camera_noise_only(client, session):
    old_noise = make_event(session, days_ago=5)
    fresh_noise = make_event(session, days_ago=0)
    person = make_event(session, event_type=EventType.PERSON_ENTERED, days_ago=5)

    login(client)
    token = csrf_from(client.get("/data").text)
    response = client.post(
        "/api/data/events/purge",
        data={
            "csrf_token": token,
            "group": "camera",
            "status": VerificationStatus.PENDING.value,
            "days": "3",
            "confirm": "OCHIRISH",
            "reason": "test",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    remaining = {row.id for row in session.query(Event).all()}
    assert old_noise.id not in remaining
    assert fresh_noise.id in remaining
    assert person.id in remaining


def test_bulk_purge_needs_the_confirmation_word(client, session):
    event = make_event(session, days_ago=5)

    login(client)
    token = csrf_from(client.get("/data").text)
    client.post(
        "/api/data/events/purge",
        data={"csrf_token": token, "group": "camera", "status": "PENDING", "days": "1", "confirm": "yoq"},
        follow_redirects=False,
    )

    assert session.get(Event, event.id) is not None


def test_bulk_purge_skips_confirmed_rows(client, session):
    event = make_event(session, days_ago=5)
    event.verification_status = VerificationStatus.CONFIRMED.value
    session.commit()

    login(client)
    token = csrf_from(client.get("/data").text)
    client.post(
        "/api/data/events/purge",
        data={
            "csrf_token": token,
            "group": "camera",
            "status": VerificationStatus.REJECTED.value,
            "days": "1",
            "confirm": "OCHIRISH",
        },
        follow_redirects=False,
    )

    assert session.get(Event, event.id) is not None


def test_orphan_stills_are_removed_and_used_ones_stay(client, session, settings):
    event = make_event(session)
    event.evidence_path = fake_evidence(settings, event.public_id)
    session.commit()
    orphan = settings.evidence_dir / "2026-10-29" / "EVT-NOSUCH.jpg"
    orphan.write_bytes(b"\xff\xd8\xff\xd9")

    login(client)
    token = csrf_from(client.get("/data").text)
    response = client.post(
        "/api/data/evidence/orphans",
        data={"csrf_token": token, "confirm": "OCHIRISH"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert not orphan.exists()
    assert (settings.evidence_dir / event.evidence_path).exists()


def test_data_page_is_admin_only(client, other_user_factory):
    login(client)
    assert client.get("/data").status_code == 200

    other_user_factory("op", "operator")
    client.post("/logout", data={})
    login(client, "op", "Operator123!")
    assert client.get("/data").status_code == 403


# -- service guards -----------------------------------------------------------
def test_service_refuses_events_with_ledger_rows(session, admin_user):
    event = make_event(session, event_type=EventType.EQUIPMENT_TAKEN_OUT, equipment_category="laptop")
    assert event.id is not None
    # The ledger candidate is created by create_event for equipment movements.
    with pytest.raises(cleanup_service.CleanupError):
        cleanup_service.delete_event(session, event, user=admin_user)
    assert session.get(Event, event.id) is not None


def test_storage_stats_counts_files_and_events(session, settings):
    event = make_event(session)
    event.evidence_path = fake_evidence(settings, event.public_id)
    session.commit()

    stats = cleanup_service.storage_stats(session)

    assert stats["events_total"] >= 1
    assert stats["evidence_files"] >= 1
    assert stats["evidence_bytes"] > 0
    assert cleanup_service.human_bytes(2048) == "2.0 KB"


# -- rejection requires a reason ---------------------------------------------
def test_reject_without_reason_is_refused_and_explained(client, session):
    event = make_event(session, event_type=EventType.PERSON_ENTERED, person_track_id=3)

    login(client)
    page = client.get("/review").text
    assert "data-reason-error" in page
    assert "Rad etish uchun sabab kiritilishi shart" in page

    token = csrf_from(page)
    response = client.post(
        f"/api/events/{event.id}/review",
        data={"csrf_token": token, "decision": "REJECTED", "reason": "   ", "next_url": "/review"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    assert session.get(Event, event.id).verification_status == VerificationStatus.PENDING.value


def test_reject_with_a_reason_is_accepted(client, session):
    event = make_event(session, event_type=EventType.PERSON_ENTERED, person_track_id=3)

    login(client)
    token = csrf_from(client.get("/review").text)
    client.post(
        f"/api/events/{event.id}/review",
        data={
            "csrf_token": token,
            "decision": "REJECTED",
            "reason": "Noto‘g‘ri aniqlangan obyekt",
            "next_url": "/review",
        },
        follow_redirects=False,
    )

    session.expire_all()
    assert session.get(Event, event.id).verification_status == VerificationStatus.REJECTED.value


# -- stale form ids must not break the review POST ----------------------------
def test_review_refuses_a_stale_asset_id(session, admin_user):
    event = make_event(session, event_type=EventType.EQUIPMENT_TAKEN_OUT, equipment_category="laptop")
    event_id = event.id

    with pytest.raises(ValueError):
        event_service.review_event(
            session,
            event,
            decision=VerificationStatus.CONFIRMED,
            user=admin_user,
            reason="test",
            asset_id=999_999,
        )

    session.rollback()
    assert session.get(Event, event_id).verification_status == VerificationStatus.PENDING.value


def test_review_post_with_a_stale_asset_id_does_not_crash(client, session):
    event = make_event(session, event_type=EventType.EQUIPMENT_TAKEN_OUT, equipment_category="laptop")
    event_id = event.id

    login(client)
    token = csrf_from(client.get("/review").text)
    response = client.post(
        f"/api/events/{event_id}/review",
        data={
            "csrf_token": token,
            "decision": "CONFIRMED",
            "reason": "",
            "asset_id": "999999",
            "next_url": "/review",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    session.expire_all()
    assert session.get(Event, event_id).verification_status == VerificationStatus.PENDING.value
