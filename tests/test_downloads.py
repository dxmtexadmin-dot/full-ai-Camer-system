"""Yuklab olish (downloads) sahifasi: Excel/CSV eksportlar va huquqlar."""

from __future__ import annotations

import io
import re

from openpyxl import load_workbook
from sqlalchemy import select

from app.models import AuditLog
from app.models.enums import EventType
from app.services import event_service, export_service


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


def test_downloads_page_lists_every_dataset(client):
    login(client)
    response = client.get("/downloads")
    assert response.status_code == 200
    for dataset in export_service.DATASETS:
        assert dataset.title in response.text
    assert "Hammasini Excel’da yuklab olish" in response.text


def test_inventory_xlsx_is_a_real_workbook(client):
    login(client)
    response = client.get("/downloads/inventory.xlsx")

    assert response.status_code == 200
    assert "spreadsheetml" in response.headers["content-type"]
    assert response.headers["content-disposition"].startswith("attachment; filename=\"face-date_inventory_")
    assert response.content[:2] == b"PK"

    workbook = load_workbook(io.BytesIO(response.content))
    worksheet = workbook.active
    headers = [cell.value for cell in worksheet[3]]
    assert headers[:3] == ["Toifa kodi", "Toifa nomi", "Boshlang‘ich"]
    assert worksheet.freeze_panes == "A4"


def test_events_csv_starts_with_bom_and_has_rows(client, session):
    event_service.create_event(session, event_type=EventType.PERSON_ENTERED, camera_code="CAM-01", person_track_id=1)
    session.commit()

    login(client)
    response = client.get("/downloads/events.csv")

    assert response.status_code == 200
    assert response.content.startswith("\ufeff".encode("utf-8"))
    text = response.content.decode("utf-8")
    assert text.splitlines()[0].lstrip("\ufeff").startswith("Hodisa ID")
    assert len(text.strip().splitlines()) == 2


def test_all_workbook_contains_one_sheet_per_dataset(client):
    login(client)
    response = client.get("/downloads/all.xlsx")
    assert response.status_code == 200

    workbook = load_workbook(io.BytesIO(response.content))
    assert len(workbook.sheetnames) == len(export_service.DATASETS)


def test_unknown_dataset_returns_404(client):
    login(client)
    response = client.get("/downloads/nope.xlsx")
    assert response.status_code == 404
    assert "eksport mavjud emas" in response.text


def test_unsupported_format_returns_400(client):
    login(client)
    response = client.get("/downloads/events.txt")
    assert response.status_code == 400
    assert ".xlsx" in response.text


def test_audit_export_is_admin_only(client, other_user_factory):
    other_user_factory("operator-one", role="operator")
    login(client, "operator-one", "Operator123!")

    assert client.get("/downloads").status_code == 200
    forbidden = client.get("/downloads/audit.xlsx")
    assert forbidden.status_code == 403
    assert "ruxsat" in forbidden.text.lower()


def test_download_is_written_to_the_audit_log(client, session):
    login(client)
    client.get("/downloads/all.xlsx")

    entries = session.scalars(select(AuditLog).where(AuditLog.action == "export.download")).all()
    assert entries
    assert entries[-1].entity_id == "all"


def test_invalid_date_range_falls_back_to_no_filter(client):
    login(client)
    response = client.get("/downloads/ledger.csv?start=not-a-date&end=2026-13-45")
    assert response.status_code == 200
    assert response.content.startswith("\ufeff".encode("utf-8"))
