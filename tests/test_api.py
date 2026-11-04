"""End-to-end HTTP smoke tests for the Uzbek dashboard and the JSON API."""

from __future__ import annotations

import csv
import io
import re

import pytest
from sqlalchemy import select

from app.models import Event, InventoryTransaction
from app.models.enums import EventType
from app.services import event_service

PAGES = [
    "/",
    "/live",
    "/employees",
    "/equipment",
    "/inventory",
    "/movements",
    "/review",
    "/reports",
    "/settings",
    "/cameras",
    "/downloads",
    "/sheets",
]


def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found in page"
    return match.group(1)


def login(client, username: str = "admin", password: str = "Admin123!") -> None:
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "next": "/", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text


def test_healthz_is_public(client):
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_anonymous_visitor_is_redirected_to_login(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_login_page_renders_uzbek_form(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert "Kirish" in response.text
    assert 'name="csrf_token"' in response.text


def test_wrong_password_is_rejected(client):
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={"username": "admin", "password": "definitely-wrong", "next": "/", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "noto‘g‘ri" in response.text


def test_login_changes_token_and_opens_dashboard(client):
    login(client)
    dashboard = client.get("/")
    assert dashboard.status_code == 200
    assert "Bosh sahifa" in dashboard.text


@pytest.mark.parametrize("path", PAGES)
def test_all_pages_render_for_admin(client, path):
    login(client)
    response = client.get(path)
    assert response.status_code == 200, path
    assert "<html" in response.text.lower()


def test_settings_page_is_admin_only(client, other_user_factory):
    other_user_factory("operator1", "operator")
    login(client, "operator1", "Operator123!")
    response = client.get("/settings")
    assert response.status_code == 403
    assert "Ruxsat yo‘q" in response.text


def test_operator_cannot_create_employee(client, other_user_factory):
    other_user_factory("operator2", "operator")
    login(client, "operator2", "Operator123!")
    # /employees itself is admin-only, so the CSRF token comes from the shell.
    token = csrf_from(client.get("/").text)
    response = client.post(
        "/api/employees",
        data={"first_name": "Ali", "last_name": "Valiyev", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 403


def test_missing_csrf_token_is_rejected(client):
    login(client)
    response = client.post(
        "/api/inventory/operation",
        data={"operation": "receive", "category_code": "monitor", "quantity": 1},
        follow_redirects=False,
    )
    assert response.status_code == 400


def test_admin_can_create_employee_and_see_it(client, session):
    login(client)
    token = csrf_from(client.get("/employees").text)
    response = client.post(
        "/api/employees",
        data={
            "first_name": "Dilnoza",
            "last_name": "Karimova",
            "department": "Buxgalteriya",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    page = client.get("/employees")
    assert "Dilnoza Karimova" in page.text
    detail = client.get("/employees/EMP001")
    assert detail.status_code == 200
    assert "Dilnoza" in detail.text


def test_inventory_operation_updates_balance(client, session):
    login(client)
    token = csrf_from(client.get("/inventory").text)
    response = client.post(
        "/api/inventory/operation",
        data={
            "operation": "initial",
            "category_code": "monitor",
            "quantity": 4,
            "reason": "Boshlang‘ich",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    rows = session.scalars(select(InventoryTransaction)).all()
    assert len(rows) == 1
    assert rows[0].is_confirmed is True

    page = client.get("/inventory")
    assert page.status_code == 200
    assert "4" in page.text


def test_review_page_confirm_flow(client, session, admin_user):
    from app.services import inventory_service

    inventory_service.record(session, category_code="monitor", transaction_type="INITIAL", quantity=3)
    event, _ = event_service.create_event(
        session,
        event_type="EQUIPMENT_TAKEN_OUT",
        camera_code="SIM-01",
        equipment_category="monitor",
        object_track_id=1,
        person_track_id=1,
    )
    session.commit()

    login(client)
    review_page = client.get("/review")
    assert review_page.status_code == 200
    assert event.public_id in review_page.text

    token = csrf_from(review_page.text)
    response = client.post(
        f"/api/events/{event.id}/review",
        data={"decision": "CONFIRMED", "reason": "Tekshirildi", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    session.expire_all()
    stored = session.get(type(event), event.id)
    assert stored.verification_status == "CONFIRMED"
    assert inventory_service.balance_for(session, "monitor").expected == 2


def test_events_api_returns_json_without_biometrics(client, session):
    event, _ = event_service.create_event(
        session, event_type="PERSON_ENTERED", camera_code="SIM-01", person_track_id=3
    )
    session.commit()

    login(client)
    response = client.get("/api/events")
    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["event_id"] == event.public_id
    assert "embedding" not in response.text.lower()


def test_csv_export_has_utf8_bom(client, session):
    event_service.create_event(session, event_type="PERSON_ENTERED", camera_code="SIM-01", person_track_id=1)
    session.commit()

    login(client)
    response = client.get("/reports/export.csv")
    assert response.status_code == 200
    assert response.content.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(response.content.decode("utf-8-sig"))))
    assert rows and rows[0][0].lower() in {"hodisa id", "event id", "public id"}


def test_unknown_page_renders_uzbek_error(client):
    login(client)
    response = client.get("/bu-sahifa-yoq")
    assert response.status_code == 404
    assert "Xatolik" in response.text


def test_evidence_endpoint_rejects_missing_file(client):
    login(client)
    response = client.get("/api/events/EVT-DOESNOTEXIST/evidence")
    assert response.status_code == 404


def test_dashboard_renders_without_cameras(client, session):
    # Disable the seeded sample camera; the page must still render.
    from app.models import Camera

    camera = session.scalars(select(Camera)).first()
    camera.is_enabled = False
    session.commit()

    login(client)
    response = client.get("/")
    assert response.status_code == 200
    assert "Kamera" in response.text


# ---------------------------------------------------------------------------
# Blank numeric inputs: the browser submits '' for empty number fields, which
# must mean "not set" instead of failing validation with 422.
# ---------------------------------------------------------------------------
def test_camera_form_accepts_blank_line_and_confidence(client, session):
    from app.models import Camera

    login(client)
    token = csrf_from(client.get("/cameras").text)
    response = client.post(
        "/api/cameras",
        data={
            "code": "CAM-BLANK",
            "name": "Chiziqsiz kamera",
            "location": "Kirish",
            "source_type": "simulation",
            "source": "",
            "line_x1": "",
            "line_y1": "",
            "line_x2": "",
            "line_y2": "",
            "confidence": "",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    camera = session.scalar(select(Camera).where(Camera.code == "CAM-BLANK"))
    assert camera is not None
    assert camera.line_x1 is None and camera.line_y2 is None
    assert camera.settings["confidence"] is None


def test_camera_form_still_validates_partial_line(client, session):
    from app.models import Camera

    login(client)
    token = csrf_from(client.get("/cameras").text)
    response = client.post(
        "/api/cameras",
        data={
            "code": "CAM-HALF",
            "name": "Yarim chiziq",
            "source_type": "simulation",
            "source": "",
            "line_x1": "10",
            "line_y1": "20",
            "line_x2": "",
            "line_y2": "",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert session.scalar(select(Camera).where(Camera.code == "CAM-HALF")) is None


def test_inventory_form_accepts_blank_delta(client, session):
    login(client)
    token = csrf_from(client.get("/inventory").text)
    response = client.post(
        "/api/inventory/operation",
        data={
            "operation": "receive",
            "category_code": "monitor",
            "quantity": "2",
            "delta": "",
            "location": "Ombor",
            "reason": "Sinov kirimi",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    assert session.scalar(select(InventoryTransaction).where(InventoryTransaction.category_code == "monitor")) is not None


def test_inventory_correction_requires_delta_value(client):
    login(client)
    token = csrf_from(client.get("/inventory").text)
    response = client.post(
        "/api/inventory/operation",
        data={
            "operation": "correct",
            "category_code": "monitor",
            "quantity": "",
            "delta": "",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    # Blank delta is a user error, not a validation crash.
    assert response.status_code == 303


def test_review_form_accepts_blank_employee_and_asset(client, session):
    event, _ = event_service.create_event(
        session, event_type="EQUIPMENT_TAKEN_OUT", camera_code="SIM-01", equipment_category="monitor"
    )
    session.commit()

    login(client)
    token = csrf_from(client.get("/review").text)
    response = client.post(
        f"/api/events/{event.id}/review",
        data={
            "decision": "CONFIRMED",
            "reason": "Sinov tasdiqlash",
            "employee_id": "",
            "asset_id": "",
            "category_code": "",
            "next_url": "/review",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303, response.text
    stored = session.get(type(event), event.id)
    session.refresh(stored)
    assert stored.verification_status == "CONFIRMED"


def test_bar_style_clamps_width():
    from app.api.deps import bar_style

    assert bar_style(42.55) == 'style="width: 42.5%"'
    assert bar_style(180) == 'style="width: 100.0%"'
    assert bar_style(-5) == 'style="width: 0.0%"'
    assert bar_style(None) == 'style="width: 0.0%"'
    assert bar_style("nope") == 'style="width: 0.0%"'


def test_reports_charts_render_valid_widths(client, session):
    event_service.create_event(
        session, event_type=EventType.PERSON_ENTERED, camera_code="CAM-01", person_track_id=1
    )
    event_service.create_event(
        session, event_type=EventType.EQUIPMENT_TAKEN_OUT, camera_code="CAM-01", person_track_id=2
    )
    session.commit()

    login(client)
    html = client.get("/reports").text

    assert 'style="wid ' not in html
    assert re.search(
        r'<div class="bar[^"]*"><span style="width: \d+\.\d%"></span></div>', html
    )


def test_nav_badge_shows_the_pending_review_count(client, session):
    event_service.create_event(
        session, event_type=EventType.PERSON_ENTERED, camera_code="CAM-01", person_track_id=1
    )
    session.commit()

    login(client)
    html = client.get("/").text

    assert '<span class="nav-badge">1</span>' in html


def test_inventory_page_keeps_ledger_totals_out_of_the_nav_badge(client, session):
    """Regression: the badge printed a dict repr on /inventory.

    ``pending_total`` on the inventory page is ``{pending_in, pending_out}`` — a
    non-empty dict is truthy, so the sidebar used to show the raw repr of that
    dict in the red badge of "Tekshirish kerak".
    """
    event_service.create_event(
        session, event_type=EventType.EQUIPMENT_TAKEN_OUT, camera_code="CAM-01", person_track_id=1
    )
    session.commit()

    login(client)
    html = client.get("/inventory").text

    assert "pending_in&#39;" not in html
    assert "{'pending_in'" not in html
    assert '<span class="nav-badge">1</span>' in html

