"""Reference photo rules: identical photos and runaway uploads."""

from __future__ import annotations

from uuid import uuid4

import cv2
import numpy as np
import pytest
from sqlalchemy import select
from app.models import AuditLog, FaceEmbedding
from app.services import employee_service
from app.services.employee_service import MAX_REFERENCE_PHOTOS


class _EmbeddingResult:
    ok = True
    warnings: list[str] = []
    quality = 0.9

    def __init__(self, seed: int) -> None:
        self.embedding = np.full(4, float(seed), dtype=np.float32)


class _Gallery:
    def reload(self, session) -> None:  # pragma: no cover - stub
        return None


class _StubFaceService:
    """Just enough of FaceService to exercise the storage logic."""

    available = True
    reason_unavailable = ""

    def __init__(self) -> None:
        self.gallery = _Gallery()
        self.calls = 0

    def create_embedding(self, image):  # noqa: ANN001 - duck typed
        self.calls += 1
        return _EmbeddingResult(self.calls)


def _jpeg(seed: int) -> bytes:
    """A small, valid JPEG whose bytes differ per *seed*."""
    image = np.full((24, 24, 3), seed % 255, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


@pytest.fixture()
def employee(session):
    # Unique code per test: the temporary data directory is shared by the session,
    # so a fixed EMP001 folder would leak photos between tests.
    code = f"T{uuid4().hex[:8].upper()}"
    return employee_service.create_employee(
        session, first_name="Test", last_name="Xodim", employee_code=code
    )


def test_identical_photo_is_not_stored_twice(session, employee, settings):
    service = _StubFaceService()
    photo = _jpeg(10)

    stored, errors, warnings = employee_service.add_reference_images(
        session, employee, [("front.jpg", photo)], face_service=service, settings=settings
    )
    assert (stored, errors, warnings) == (1, [], [])

    # The browser may resend the same form (F5 / slow network).
    stored, errors, warnings = employee_service.add_reference_images(
        session, employee, [("front.jpg", photo)], face_service=service, settings=settings
    )
    assert stored == 0
    assert errors == []
    assert "avval yuklangan" in warnings[0]
    assert session.scalar(select(FaceEmbedding).where(FaceEmbedding.employee_id == employee.id)) is not None
    assert len(session.scalars(select(FaceEmbedding).where(FaceEmbedding.employee_id == employee.id)).all()) == 1


def test_reference_photo_limit_is_enforced(session, employee, settings):
    service = _StubFaceService()
    uploads = [(f"photo_{index}.jpg", _jpeg(index + 1)) for index in range(MAX_REFERENCE_PHOTOS + 2)]

    stored, errors, _ = employee_service.add_reference_images(
        session, employee, uploads, face_service=service, settings=settings
    )

    assert stored == MAX_REFERENCE_PHOTOS
    assert errors and "Chegara" in errors[0]
    assert len(session.scalars(select(FaceEmbedding).where(FaceEmbedding.employee_id == employee.id)).all()) == MAX_REFERENCE_PHOTOS


def test_next_reference_name_never_overwrites_existing_file(session, employee, settings):
    folder = employee_service.reference_dir(employee, settings)
    for leftover in folder.glob("reference_*"):
        leftover.unlink()
    for index in (1, 2, 3):
        (folder / f"reference_{index:02d}.jpg").write_bytes(_jpeg(index))

    # reference_02 deleted by the operator -> the gap must not be reused.
    (folder / "reference_02.jpg").unlink()

    target = employee_service._next_reference_path(employee, ".jpg", settings)
    assert target.name == "reference_04.jpg"


def test_deleting_a_reference_is_audited(session, employee, settings):
    service = _StubFaceService()
    employee_service.add_reference_images(
        session, employee, [("front.jpg", _jpeg(20))], face_service=service, settings=settings
    )
    embedding = session.scalars(
        select(FaceEmbedding).where(FaceEmbedding.employee_id == employee.id)
    ).one()

    assert employee_service.delete_reference(session, employee, embedding.id, settings=settings)
    assert session.get(FaceEmbedding, embedding.id) is None

    actions = [row.action for row in session.scalars(select(AuditLog)).all()]
    assert "employee.reference_deleted" in actions
