"""\"Kameradan rasm olish\" endpoint: kadr -> yuz -> shablon (model va kamerasiz)."""

from __future__ import annotations

import re
from pathlib import Path
from uuid import uuid4

import numpy as np
import pytest
from sqlalchemy import select

from app.models import FaceEmbedding
from app.services import employee_service
from app.services.face_service import DetectedFace
from app.services.types import BBox


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


class _EmbeddingResult:
    ok = True
    warnings: list[str] = []
    quality = 0.9
    faces_found = 1

    def __init__(self, seed: int) -> None:
        self.embedding = np.full(4, float(seed), dtype=np.float32)


class _Gallery:
    def reload(self, session) -> None:  # pragma: no cover - stub
        return None


class _StubFaceService:
    available = True
    reason_unavailable = ""

    def __init__(self) -> None:
        self.gallery = _Gallery()
        self.calls = 0
        self.faces: list[DetectedFace] = []

    def detect_faces(self, image):  # noqa: ANN001 - duck typed
        return list(self.faces)

    def status(self) -> dict:
        return {"available": True, "reason": None, "gallery_embeddings": 0}

    def create_embedding(self, image):  # noqa: ANN001 - duck typed
        self.calls += 1
        return _EmbeddingResult(len(self.faces) + self.calls)


class _Worker:
    def __init__(self, frame: np.ndarray) -> None:
        self._frame = frame

    def latest_frame(self):  # noqa: ANN201 - duck typed
        return self._frame.copy()


class _Monitor:
    def __init__(self, worker) -> None:  # noqa: ANN001 - duck typed
        self._worker = worker
        self.refreshed = 0

    def get_worker(self, code: str):  # noqa: ANN201 - duck typed
        return self._worker if code == "CAM" else None

    def refresh_gallery(self) -> int:
        self.refreshed += 1
        return 1


def _face(box: tuple[float, float, float, float]) -> DetectedFace:
    return DetectedFace(
        bbox=BBox(*box),
        embedding=np.ones(4, dtype=np.float32),
        det_score=0.95,
    )


@pytest.fixture()
def employee(session):
    code = f"C{uuid4().hex[:8].upper()}"
    row = employee_service.create_employee(session, first_name="Kamera", last_name="Test", employee_code=code)
    # Commit before the HTTP call: SQLite locks the database for writers.
    session.commit()
    return row


def _prepare(monkeypatch, face: _StubFaceService, worker: _Worker) -> _Monitor:
    monitor = _Monitor(worker)
    monkeypatch.setattr("app.api.employees.get_face_service", lambda: face)
    monkeypatch.setattr("app.api.employees.get_monitor", lambda: monitor)
    monkeypatch.setattr("app.services.employee_service.get_face_service", lambda settings=None: face)
    return monitor


def test_capture_stores_camera_face_as_template(client, session, employee, monkeypatch):
    face = _StubFaceService()
    face.faces = [_face((20, 20, 120, 120))]
    monitor = _prepare(monkeypatch, face, _Worker(np.full((240, 320, 3), 140, dtype=np.uint8)))

    login(client)
    token = csrf_from(client.get(f"/employees/{employee.employee_code}").text)
    response = client.post(
        f"/api/employees/{employee.id}/photos/capture",
        data={"camera": "CAM", "csrf_token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303

    session.expire_all()
    rows = session.scalars(select(FaceEmbedding).where(FaceEmbedding.employee_id == employee.id)).all()
    assert len(rows) == 1
    assert rows[0].source_image.endswith(".jpg")
    stored = Path(rows[0].source_image).name
    assert (employee_service.reference_dir(employee, None) / stored).exists()
    assert monitor.refreshed == 1


def test_capture_reports_missing_face(client, session, employee, monkeypatch):
    face = _StubFaceService()  # kadrda yuz yo'q
    _prepare(monkeypatch, face, _Worker(np.full((240, 320, 3), 12, dtype=np.uint8)))

    login(client)
    page = client.get(f"/employees/{employee.employee_code}")
    response = client.post(
        f"/api/employees/{employee.id}/photos/capture",
        data={"camera": "CAM", "csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303

    session.expire_all()
    assert session.scalars(select(FaceEmbedding).where(FaceEmbedding.employee_id == employee.id)).all() == []


def test_capture_reports_missing_frame(client, session, employee, monkeypatch):
    face = _StubFaceService()
    monitor = _Monitor(None)
    monkeypatch.setattr("app.api.employees.get_face_service", lambda: face)
    monkeypatch.setattr("app.api.employees.get_monitor", lambda: monitor)

    login(client)
    page = client.get(f"/employees/{employee.employee_code}")
    response = client.post(
        f"/api/employees/{employee.id}/photos/capture",
        data={"camera": "CAM", "csrf_token": csrf_from(page.text)},
        follow_redirects=False,
    )
    assert response.status_code == 303
