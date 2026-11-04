"""New equipment type (\"/equipment\" -> \"Yangi texnika toifasi\") and the crop helper."""

from __future__ import annotations

import re

import numpy as np
import pytest
from sqlalchemy import select

from app.models import AuditLog, EquipmentCategory
from app.services import equipment_service
from app.utils.images import padded_crop


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


def test_create_category_appends_new_type(session, admin_user):
    before = [(item.code, item.sort_order) for item in equipment_service.categories(session)]

    category = equipment_service.create_category(
        session,
        code="drill",
        label_uz="Perforator",
        yolo_labels=["drill", "Hammer drill"],
        min_confidence=0.5,
        user=admin_user,
    )
    session.commit()

    assert category.code == "drill"
    assert category.yolo_labels == ["drill", "Hammer drill"]
    assert category.min_confidence == 0.5
    assert category.is_person is False
    assert category.sort_order > max(order for _, order in before)

    # Mavjud toifalar o'zgarmagan bo'lishi kerak.
    current = [(item.code, item.sort_order) for item in equipment_service.categories(session)]
    assert current[: len(before)] == before
    assert current[-1][0] == "drill"

    log = session.scalar(select(AuditLog).where(AuditLog.action == "category.create"))
    assert log is not None and log.entity_id == "drill"


def test_create_category_validates_input(session):
    equipment_service.create_category(session, code="drill", label_uz="Perforator", yolo_labels=["drill"])
    session.flush()

    with pytest.raises(ValueError):
        equipment_service.create_category(session, code="drill", label_uz="Takror", yolo_labels=["x"])
    with pytest.raises(ValueError):
        equipment_service.create_category(session, code="Drill Press", label_uz="Stanok", yolo_labels=["x"])
    with pytest.raises(ValueError):
        equipment_service.create_category(session, code="press", label_uz="   ", yolo_labels=["x"])
    with pytest.raises(ValueError):
        equipment_service.create_category(session, code="press", label_uz="Press", yolo_labels=["", "  "])
    with pytest.raises(ValueError):
        equipment_service.create_category(session, code="press", label_uz="Press", yolo_labels=["x"], min_confidence=1.5)


def test_admin_adds_category_through_form(client, session):
    login(client)
    token = csrf_from(client.get("/equipment").text)
    response = client.post(
        "/api/categories",
        data={
            "code": "drill",
            "label_uz": "Perforator",
            "yolo_labels": "drill, hammer drill",
            "min_confidence": "0.4",
            "csrf_token": token,
        },
        follow_redirects=False,
    )
    assert response.status_code == 303

    session.expire_all()
    stored = session.scalar(select(EquipmentCategory).where(EquipmentCategory.code == "drill"))
    assert stored is not None
    assert stored.label_uz == "Perforator"
    assert stored.yolo_labels == ["drill", "hammer drill"]
    assert stored.min_confidence == pytest.approx(0.4)


def test_operator_cannot_add_category(client, other_user_factory):
    other_user_factory("operator4", "operator")
    login(client, "operator4", "Operator123!")
    token = csrf_from(client.get("/").text)
    response = client.post(
        "/api/categories",
        data={"code": "drill", "label_uz": "Perforator", "yolo_labels": "drill", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 403


class _Box:
    def __init__(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.x1, self.y1, self.x2, self.y2 = x1, y1, x2, y2


def test_padded_crop_keeps_margin_and_clamps_to_frame():
    frame = np.zeros((100, 120, 3), dtype=np.uint8)
    crop = padded_crop(frame, _Box(40, 40, 60, 60), margin=0.5)
    assert crop is not None
    assert crop.shape[:2] == (40, 40)

    edge = padded_crop(frame, _Box(0, 0, 30, 30), margin=1.0)
    assert edge is not None
    assert edge.shape[:2] == (60, 60)

    # Kadr chetidan chiqib ketmaydi.
    corner = padded_crop(frame, _Box(90, 70, 120, 100), margin=0.5)
    assert corner is not None
    assert corner.shape[:2] == (45, 45)

    assert padded_crop(frame, _Box(5, 5, 5, 5)) is None
    assert padded_crop(np.zeros((0, 0, 3), dtype=np.uint8), _Box(1, 1, 90, 90)) is None
