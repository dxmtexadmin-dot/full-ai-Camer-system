"""QR inventory, assignment, repair and camera-observation checks (§14).

The HTTP tests run against the throw-away database from ``conftest`` and use a
real ``qrcode`` render; the scannability checks decode the generated PNG with
OpenCV, so "the label is printable" is actually measured, not assumed.
"""

from __future__ import annotations

import io
import re

import pytest
from PIL import Image

from app.models import AssetMovementEvent, Camera, Employee, EquipmentAsset
from app.models.enums import (
    AssetMovementType,
    CameraStatus,
    EquipmentStatus,
    Role,
    VerificationStatus,
)
from app.services import asset_service, qr_service, schema_migration

SIZES = ["20x20", "25x25", "30x30", "40x40", "50x30", "60x40"]


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def csrf_from(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "CSRF token not found in page"
    return match.group(1)


def login(client, username: str = "admin", password: str = "Admin123!") -> None:
    token = csrf_from(client.get("/login").text)
    response = client.post(
        "/login",
        data={"username": username, "password": password, "next": "/", "csrf_token": token},
    )
    assert response.status_code == 200


def make_employee(session, code: str, first: str = "Ali", last: str = "Valiyev", **extra) -> Employee:
    employee = Employee(
        employee_code=code,
        first_name=first,
        last_name=last,
        department=extra.pop("department", "Buxgalteriya"),
        is_active=extra.pop("is_active", True),
        **extra,
    )
    session.add(employee)
    session.commit()
    return employee


def add_asset(client, code: str = "PC-001", name: str = "Buxgalteriya kompyuteri", **extra) -> None:
    token = csrf_from(client.get("/assets").text)
    response = client.post(
        "/api/assets",
        data={
            "category_code": extra.pop("category_code", "desktop_pc"),
            "asset_code": code,
            "name": name,
            "room": extra.pop("room", "201-xona"),
            "department": extra.pop("department", "Buxgalteriya"),
            "model": extra.pop("model", "OptiPlex 7010"),
            **extra,
            "csrf_token": token,
        },
    )
    assert response.status_code == 200, response.text


def decode_qr(png_bytes: bytes) -> str:
    cv2 = pytest.importorskip("cv2")
    import numpy as np

    image = np.array(Image.open(io.BytesIO(png_bytes)).convert("RGB"))[:, :, ::-1]
    detector = cv2.QRCodeDetector()
    text, points, _ = detector.detectAndDecode(image)
    assert points is not None, "OpenCV did not locate a QR code in the image"
    return text


# --------------------------------------------------------------------------- #
# Registry, QR creation, printing
# --------------------------------------------------------------------------- #
def test_assets_page_renders_empty_registry(client):
    login(client)
    response = client.get("/assets")
    assert response.status_code == 200
    assert "Qurilmalar" in response.text or "Qurilma" in response.text


def test_admin_adds_asset_and_qr_code_is_issued(client, session, admin_user):
    login(client)
    add_asset(client)

    asset = session.query(EquipmentAsset).filter(EquipmentAsset.asset_code == "PC-001").one()
    qr = asset_service.get_qr(session, asset.id)
    assert qr is not None
    assert re.fullmatch(r"[0-9a-f]{32}", qr.token), "token must be an opaque random value"
    # No secret leaks into the payload: it is only the public URL of the page.
    assert "PC-001" not in qr.token
    assert asset.name == "Buxgalteriya kompyuteri"
    assert asset.room == "201-xona"


def test_duplicate_inventory_number_is_rejected(client, session):
    login(client)
    add_asset(client, code="PC-001")
    add_asset(client, code="PC-001")  # same number again -> flash error, no second row

    assert session.query(EquipmentAsset).filter(EquipmentAsset.asset_code == "PC-001").count() == 1


def test_qr_png_downloads_as_attachment(client, asset):
    login(client)
    response = client.get(f"/assets/{asset.id}/qr.png?download=1")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert "attachment" in response.headers["content-disposition"]
    assert f"QR-{asset.asset_code}.png" in response.headers["content-disposition"]


def test_qr_png_decodes_to_the_device_page(client, session, asset):
    """Simulates a phone scan: decode the printed PNG and follow its URL."""
    login(client)
    token = asset_service.get_qr(session, asset.id).token
    payload = decode_qr(client.get(f"/assets/{asset.id}/qr.png").content)
    assert payload == f"http://testserver/qr/{token}"

    opened = client.get(f"/qr/{token}")
    assert opened.status_code == 200
    assert asset.asset_code in opened.text


def test_label_png_renders_for_every_preset_size(client, session, asset):
    login(client)
    expected = f"http://testserver/qr/{asset_service.get_qr(session, asset.id).token}"
    for size in SIZES:
        response = client.get(f"/assets/{asset.id}/label.png?size={size}")
        assert response.status_code == 200, size
        image = Image.open(io.BytesIO(response.content))
        assert image.width > 0 and image.height > 0
        assert decode_qr(response.content) == expected, f"{size} label is not scannable"

def test_custom_label_size_is_accepted_and_validated(client, asset):
    login(client)
    response = client.get(f"/assets/{asset.id}/label.png?w=45&h=35")
    assert response.status_code == 200
    width, height = qr_service.parse_label_size(None, 45, 35)
    expected = (qr_service.mm_to_px(width), qr_service.mm_to_px(height))
    assert Image.open(io.BytesIO(response.content)).size == expected

    for bad in ("size=5x5", "w=400&h=400", "size=abc"):
        assert client.get(f"/assets/{asset.id}/label.png?{bad}").status_code == 400


def test_label_print_page_renders_with_size_css(client, asset):
    login(client)
    response = client.get(f"/assets/{asset.id}/print?size=40x40")
    assert response.status_code == 200
    assert "100%" in response.text, "the print page must tell the user to print at 100 %"
    assert f"/assets/{asset.id}/label.css" in response.text

    css = client.get(f"/assets/{asset.id}/label.css?size=40x40")
    assert css.status_code == 200
    assert "40mm 40mm" in css.text


def test_a4_sheet_prints_several_labels(client, session, admin_user):
    login(client)
    add_asset(client, code="PC-001")
    add_asset(client, code="PC-002", category_code="monitor", name="Monitor")
    ids = [item.id for item in session.query(EquipmentAsset).order_by(EquipmentAsset.id).all()]

    params = [("asset_ids", str(asset_id)) for asset_id in ids] + [("size", "30x30")]
    response = client.get("/assets-print", params=params)
    assert response.status_code == 200
    assert "100%" in response.text
    for asset_id in ids:
        assert f"/assets/{asset_id}/label.png" in response.text

    css = client.get("/assets-print.css?size=30x30&margin=10&gap=3")
    assert css.status_code == 200
    assert "@page" in css.text and "A4" in css.text
    assert "grid-template-columns" in css.text


def test_a4_capacity_matches_the_page_size():
    small_cols, small_rows = qr_service.a4_label_capacity((20.0, 20.0), margin_mm=10, gap_mm=3)
    large_cols, large_rows = qr_service.a4_label_capacity((60.0, 40.0), margin_mm=10, gap_mm=3)
    assert small_cols * small_rows > large_cols * large_rows
    assert (small_cols, small_rows) == (8, 12)  # 190 mm usable width / 23 mm per label


def test_label_design_parts_can_be_switched_off(client, asset):
    login(client)
    only_qr = client.get(f"/assets/{asset.id}/print?parts_submitted=1&part=qr")
    assert only_qr.status_code == 200
    assert 'value="qr" checked' in only_qr.text
    assert 'value="name" checked' not in only_qr.text
    assert 'value="org" checked' not in only_qr.text

    everything = client.get(
        f"/assets/{asset.id}/print?parts_submitted=1&part=qr&part=code&part=name&part=model&part=resp&part=org"
    )
    assert everything.status_code == 200
    for part in ("code", "name", "model", "resp", "org"):
        assert f'value="{part}" checked' in everything.text, part


def test_qr_token_opens_the_device_page_and_is_recorded(client, session, asset):
    login(client)
    token = asset_service.get_qr(session, asset.id).token
    response = client.get(f"/qr/{token}")
    assert response.status_code == 200
    assert asset.asset_code in response.text

    session.expire_all()
    actions = [row.action for row in asset_service.usage_history(session, asset.id)]
    assert "QR_SCANNED" in actions


def test_unknown_qr_token_returns_to_the_registry(client):
    login(client)
    response = client.get("/qr/deadbeefdeadbeefdeadbeefdeadbeef")
    assert response.status_code == 200
    assert "tanilmadi" in response.text or "Qurilma" in response.text


def test_reissue_changes_the_token_and_retires_the_old_label(client, session, asset):
    login(client)
    old = asset_service.get_qr(session, asset.id).token
    token = csrf_from(client.get(f"/assets/{asset.id}").text)
    client.post(f"/api/assets/{asset.id}/qr/reissue", data={"csrf_token": token})

    session.expire_all()
    assert qr_service.find_by_token(session, old) is None
    assert qr_service.find_by_token(session, asset_service.get_qr(session, asset.id).token) is not None


def test_mark_printed_increments_the_reprint_counter(client, session, asset):
    login(client)
    token = csrf_from(client.get(f"/assets/{asset.id}").text)
    first = client.post(f"/api/assets/{asset.id}/qr/mark-printed?size=30x30", headers={"X-CSRF-Token": token})
    assert first.status_code == 200 and first.json()["ok"] is True

    session.expire_all()
    assert asset_service.get_qr(session, asset.id).reprint_count == 1


# --------------------------------------------------------------------------- #
# Assignment / transfer / technician / repair
# --------------------------------------------------------------------------- #
def test_assignment_then_transfer_keeps_the_previous_user(client, session, asset, admin_user):
    login(client)
    first = make_employee(session, "E-001", "Ali", "Valiyev")
    second = make_employee(session, "E-002", "Nodira", "Karimova")
    token = csrf_from(client.get(f"/assets/{asset.id}").text)

    client.post(
        f"/api/assets/{asset.id}/assign",
        data={"employee_id": str(first.id), "position": "Buxgalter", "csrf_token": token},
    )
    client.post(
        f"/api/assets/{asset.id}/assign",
        data={"employee_id": str(second.id), "position": "Kassir", "csrf_token": token},
    )

    session.expire_all()
    item = session.get(EquipmentAsset, asset.id)
    assert item.responsible_name == "Nodira Karimova"

    history = asset_service.assignment_history(session, asset.id)
    assert [row.employee_name for row in history][:2] == ["Nodira Karimova", "Ali Valiyev"]
    assert history[1].ended_at is not None, "the previous owner's record must be closed, not deleted"

    actions = [row.action for row in asset_service.usage_history(session, asset.id)]
    assert "TRANSFERRED" in actions


def test_operator_may_not_assign_or_create(client, session, other_user_factory, asset):
    make_employee(session, "E-001", "Ali", "Valiyev")
    other_user_factory("operator1", Role.OPERATOR.value)
    login(client, "operator1", "Operator123!")
    token = csrf_from(client.get("/assets").text)

    created = client.post(
        "/api/assets",
        data={"category_code": "monitor", "asset_code": "MON-900", "csrf_token": token},
    )
    assert created.status_code == 403
    assert session.query(EquipmentAsset).filter(EquipmentAsset.asset_code == "MON-900").count() == 0

    assigned = client.post(
        f"/api/assets/{asset.id}/assign",
        data={"employee_id": "1", "csrf_token": token},
    )
    assert assigned.status_code == 403
    session.expire_all()
    assert session.get(EquipmentAsset, asset.id).responsible_employee_id is None


def test_viewer_cannot_open_the_registry_mutations(client, other_user_factory, asset):
    other_user_factory("viewer1", Role.VIEWER.value)
    login(client, "viewer1", "Operator123!")

    assert client.get("/assets").status_code == 200
    token = csrf_from(client.get("/assets").text)
    response = client.post(f"/api/assets/{asset.id}/release", data={"csrf_token": token})
    assert response.status_code == 403


def test_technician_handoff_and_repair_history(client, session, asset, admin_user):
    login(client)
    technician = make_employee(session, "T-001", "Sardor", "Toshmatov")
    token = csrf_from(client.get(f"/assets/{asset.id}").text)

    client.post(
        f"/api/assets/{asset.id}/technician",
        data={"technician_employee_id": str(technician.id), "csrf_token": token},
    )
    session.expire_all()
    assert session.get(EquipmentAsset, asset.id).technician_name == "Sardor Toshmatov"

    client.post(f"/api/assets/{asset.id}/repair/start", data={"reason": "Ekran ishlamaydi", "csrf_token": token})
    session.expire_all()
    repair = asset_service.repair_history(session, asset.id)[0]
    assert repair.status == "IN_PROGRESS"
    assert repair.reason == "Ekran ishlamaydi"
    assert session.get(EquipmentAsset, asset.id).status == EquipmentStatus.MAINTENANCE.value

    client.post(
        f"/api/assets/{asset.id}/repair/{repair.id}/finish",
        data={"result": "Kabel almashtirildi", "csrf_token": token},
    )
    client.post(f"/api/assets/{asset.id}/repair/{repair.id}/return", data={"note": "Egasiga topshirildi", "csrf_token": token})

    session.expire_all()
    repair = asset_service.repair_history(session, asset.id)[0]
    assert repair.status == "RETURNED"
    assert "Kabel almashtirildi" in (repair.result or "")
    assert repair.returned_at is not None

    actions = [row.action for row in asset_service.usage_history(session, asset.id)]
    assert "REPAIR_RECEIVED" in actions and "REPAIR_RETURNED" in actions
    # The repair never changed the official owner.
    assert session.get(EquipmentAsset, asset.id).responsible_employee_id is None


def test_repair_requires_a_reason(client, session, asset):
    login(client)
    token = csrf_from(client.get(f"/assets/{asset.id}").text)
    client.post(f"/api/assets/{asset.id}/repair/start", data={"reason": "   ", "csrf_token": token})

    session.expire_all()
    assert asset_service.repair_history(session, asset.id) == []


def test_technician_only_sees_own_devices(client, session, other_user_factory, asset, admin_user):
    from app.models import User
    from app.utils.security import hash_password

    specialist = User(
        username="tech1",
        full_name="Sardor Toshmatov",
        password_hash=hash_password("Operator123!"),
        role=Role.TECHNICIAN.value,
        is_active=True,
    )
    session.add(specialist)
    session.commit()

    other = asset_service.create_asset(
        session, category_code="monitor", asset_code="MON-777", name="Boshqa monitor", user=admin_user
    )
    asset_service.assign_technician(session, asset, technician_name="Sardor Toshmatov", user=admin_user)
    session.commit()

    login(client, "tech1", "Operator123!")
    page = client.get("/assets")
    assert page.status_code == 200
    assert asset.asset_code in page.text
    assert other.asset_code not in page.text


# --------------------------------------------------------------------------- #
# Camera observations
# --------------------------------------------------------------------------- #
def test_identified_removal_is_recorded_without_touching_the_owner(client, session, asset):
    login(client)
    movement = asset_service.observe_movement(
        session,
        camera_code="CAM-01",
        movement_type=AssetMovementType.TAKEN_OUT,
        asset=asset,
        confidence=0.82,
    )
    session.commit()

    assert movement.movement_type == AssetMovementType.TAKEN_OUT.value
    assert movement.asset_id == asset.id
    assert movement.verification_status == VerificationStatus.AUTO.value
    assert session.get(EquipmentAsset, asset.id).responsible_employee_id is None

    detail = client.get(f"/assets/{asset.id}")
    assert detail.status_code == 200
    assert "Joyidan olib chiqildi" in detail.text


def test_unclear_camera_event_goes_to_the_review_queue(client, session, asset, admin_user):
    login(client)
    employee = make_employee(session, "E-020", "Nodira", "Karimova")
    asset_service.assign_asset(session, asset, employee_id=employee.id, user=admin_user)
    movement = asset_service.record_unidentified(
        session, camera_code="CAM-01", detected_category="monitor", confidence=0.4
    )
    session.commit()
    assert movement.verification_status == VerificationStatus.PENDING.value

    queue = client.get("/assets/events")
    assert queue.status_code == 200
    assert "Aniqlanmagan" in queue.text

    token = csrf_from(queue.text)
    client.post(f"/api/asset-movements/{movement.id}/review", data={"decision": "confirm", "csrf_token": token})

    session.expire_all()
    assert session.get(AssetMovementEvent, movement.id).verification_status == VerificationStatus.CONFIRMED.value
    # A camera event never rewrites the official owner.
    assert session.get(EquipmentAsset, asset.id).responsible_employee_id == employee.id


def test_empty_monitoring_list_explains_why_it_is_empty(client, session, asset):
    login(client)

    # Nothing recorded yet: the page must say how a record is produced, not just "empty".
    page = client.get("/assets/events")
    assert page.status_code == 200
    assert "Hozircha tasdiqlash kutilayotgan yozuv yo'q" in page.text
    everything = client.get("/assets/events", params={"pending": "no"})
    assert "Kuzatuv yozuvlari hali yo'q" in everything.text

    record = asset_service.record_unidentified(session, camera_code="CAM-09")
    session.commit()

    # One record waits for review: the queue lists it.
    queue = client.get("/assets/events")
    assert "Aniqlanmagan" in queue.text

    # Once reviewed, the queue explains itself instead of looking broken.
    token = csrf_from(queue.text)
    client.post(f"/api/asset-movements/{record.id}/review", data={"decision": "confirm", "csrf_token": token})
    reviewed = client.get("/assets/events")
    assert "Tasdiqlash kutilayotgan yozuv yo'q" in reviewed.text
    assert "jami 1 ta yozuv" in reviewed.text


def test_camera_reads_the_qr_and_links_the_device(session, asset):
    token = asset_service.get_qr(session, asset.id).token
    found = asset_service.scan_qr(session, token, camera_code="CAM-02")
    session.commit()

    assert found is not None and found.id == asset.id
    movements = asset_service.movement_history(session, asset.id)
    assert movements[0].movement_type == AssetMovementType.QR_SCANNED.value
    assert movements[0].camera_code == "CAM-02"
    assert movements[0].verification_status == VerificationStatus.CONFIRMED.value


def test_unreadable_qr_creates_an_unclear_event(session, asset):
    assert asset_service.scan_qr(session, "0" * 32, camera_code="CAM-03") is None
    movement = asset_service.record_unidentified(session, camera_code="CAM-03", detected_category="monitor")
    session.commit()

    assert movement is not None
    assert movement.asset_id is None
    assert movement.verification_status == VerificationStatus.PENDING.value
    assert "identifikatsiya" in (movement.notes or "").lower()


def test_duplicate_camera_frames_collapse_inside_one_minute(session, asset):
    first, created_first = asset_service.record_movement(
        session, camera_code="CAM-04", movement_type=AssetMovementType.TAKEN_OUT, asset=asset
    )
    session.commit()
    second, created_second = asset_service.record_movement(
        session, camera_code="CAM-04", movement_type=AssetMovementType.TAKEN_OUT, asset=asset
    )
    session.commit()

    assert created_first is True
    assert created_second is False, "the same fact inside one minute must not be stored twice"
    assert second.id == first.id

    # The review event collapses with it, so the queue holds one row, not a stream.
    asset_service.observe_movement(
        session, camera_code="CAM-04", movement_type=AssetMovementType.MOVED, asset=asset
    )
    asset_service.observe_movement(
        session, camera_code="CAM-04", movement_type=AssetMovementType.MOVED, asset=asset
    )
    session.commit()
    assert session.query(AssetMovementEvent).filter(
        AssetMovementEvent.movement_type == AssetMovementType.MOVED.value
    ).count() == 1


def test_camera_link_and_down_camera_accounting(client, session, asset):
    login(client)
    token = csrf_from(client.get(f"/assets/{asset.id}").text)
    client.post(
        f"/api/assets/{asset.id}/camera",
        data={"camera_code": "CAM-05", "zone": "Kirish", "csrf_token": token},
    )
    session.expire_all()
    assert [link.camera_code for link in asset_service.camera_links(session, asset.id)] == ["CAM-05"]

    before = asset_service.stats(session)
    session.add(
        Camera(code="CAM-05", name="Kirish kamerasi", source_type="rtsp", source="rtsp://x", status=CameraStatus.ONLINE.value)
    )
    session.add(
        Camera(code="CAM-06", name="Ombor kamerasi", source_type="rtsp", source="rtsp://y", status=CameraStatus.OFFLINE.value)
    )
    session.commit()

    counters = asset_service.stats(session)
    assert counters["cameras_total"] == before["cameras_total"] + 2
    assert counters["cameras_online"] == before["cameras_online"] + 1
    assert counters["cameras_down"] == before["cameras_down"] + 1, (
        "a disconnected camera must be reported, not counted as monitored"
    )

    dashboard = client.get("/assets")
    assert dashboard.status_code == 200


def test_dashboard_counters_reflect_the_registry(client, session, asset, admin_user):
    login(client)
    employee = make_employee(session, "E-010", "Nodira", "Karimova")
    asset_service.assign_asset(session, asset, employee_id=employee.id, user=admin_user)
    session.commit()

    counters = asset_service.stats(session)
    assert counters["assets_total"] == 1
    assert counters["assets_assigned"] == 1
    assert counters["assets_unassigned"] == 0


# --------------------------------------------------------------------------- #
# Migration
# --------------------------------------------------------------------------- #
LEGACY_SCHEMA = (
    """
    CREATE TABLE equipment_assets (
        id INTEGER PRIMARY KEY,
        asset_code VARCHAR(48) NOT NULL UNIQUE,
        category_id INTEGER,
        brand VARCHAR(80),
        model VARCHAR(120),
        serial_number VARCHAR(120),
        asset_tag VARCHAR(64),
        location VARCHAR(120),
        status VARCHAR(24),
        notes TEXT,
        created_at DATETIME,
        updated_at DATETIME
    )
    """,
    """
    INSERT INTO equipment_assets (id, asset_code, category_id, brand, model, location, status)
    VALUES (1, 'PC-000', 1, 'Dell', 'OptiPlex', '101-xona', 'IN_STOCK')
    """,
)


def test_migration_upgrades_and_rolls_back_a_legacy_database(tmp_path):
    from sqlalchemy import create_engine, text

    engine = create_engine(f"sqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    with engine.begin() as conn:
        for statement in LEGACY_SCHEMA:
            conn.execute(text(statement))
        before = {row[1] for row in conn.execute(text("PRAGMA table_info(equipment_assets)"))}

    assert [item.id for item in schema_migration.pending_migrations(engine)] == [
        "001_equipment_asset_profile"
    ]

    applied = schema_migration.apply_migrations(engine, backup=False)
    assert applied == ["001_equipment_asset_profile"]
    with engine.begin() as conn:
        after = {row[1] for row in conn.execute(text("PRAGMA table_info(equipment_assets)"))}
        row = conn.execute(text("SELECT asset_code, name, is_active FROM equipment_assets")).one()
    assert after - before == {column.name for column in schema_migration.MIGRATIONS[0].columns}
    assert row == ("PC-000", "", 1), "the existing row and its values must survive"

    # Idempotent on the second startup.
    assert schema_migration.apply_migrations(engine, backup=False) == []

    dropped = schema_migration.rollback_migration(engine, "001_equipment_asset_profile", backup=False)
    assert len(dropped) == len(schema_migration.MIGRATIONS[0].columns)
    with engine.begin() as conn:
        restored = {row[1] for row in conn.execute(text("PRAGMA table_info(equipment_assets)"))}
        assert conn.execute(text("SELECT COUNT(*) FROM equipment_assets")).scalar() == 1
    assert restored == before


# --------------------------------------------------------------------------- #
# Regression: the pre-existing features still work
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", ["/", "/equipment", "/inventory", "/reports", "/employees"])
def test_existing_pages_still_render(client, path):
    login(client)
    assert client.get(path).status_code == 200


def test_existing_events_api_still_works(client, session, asset):
    asset_service.observe_movement(
        session, camera_code="CAM-09", movement_type=AssetMovementType.TAKEN_OUT, asset=asset
    )
    session.commit()

    login(client)
    response = client.get("/api/events")
    assert response.status_code == 200
    assert isinstance(response.json(), (list, dict))


def test_statuses_remain_the_original_enum(asset, session):
    assert asset.status in {item.value for item in EquipmentStatus}
