"""Bulk enrolment script: name parsing, idempotency and the duplicate-person check."""

from __future__ import annotations

import sys
from pathlib import Path

import cv2
import numpy as np
import pytest
from sqlalchemy import select

from app.models import Employee
from app.services import employee_service

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import enroll_employees  # noqa: E402


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
    available = True
    reason_unavailable = ""

    def __init__(self) -> None:
        self.gallery = _Gallery()
        self.calls = 0

    def create_embedding(self, image):  # noqa: ANN001 - duck typed
        self.calls += 1
        return _EmbeddingResult(self.calls)


def _jpeg(seed: int) -> bytes:
    image = np.full((24, 24, 3), seed % 255, dtype=np.uint8)
    ok, buffer = cv2.imencode(".jpg", image)
    assert ok
    return buffer.tobytes()


def _person(folder: Path, seeds: tuple[int, ...]) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for index, seed in enumerate(seeds, start=1):
        (folder / f"{index}.jpg").write_bytes(_jpeg(seed))


@pytest.fixture(autouse=True)
def stub_face_service(monkeypatch):
    service = _StubFaceService()
    monkeypatch.setattr(employee_service, "get_face_service", lambda *args, **kwargs: service)
    return service


@pytest.fixture(autouse=True)
def clean_reference_folders(settings):
    """The temporary data directory is shared, so EMP001..EMP003 must start empty."""
    for code in ("EMP001", "EMP002", "EMP003"):
        folder = settings.employees_dir / code
        if not folder.exists():
            continue
        for path in folder.glob("reference_*"):
            path.unlink()


def test_split_name_defaults_to_family_first():
    assert enroll_employees._split_name("Anvarov Anvar", False) == ("Anvar", "Anvarov")
    assert enroll_employees._split_name("Anvar_Anvarov", True) == ("Anvar", "Anvarov")
    assert enroll_employees._split_name("Anvar Anvar o‘g‘li", False) == ("Anvar o‘g‘li", "Anvar")
    assert enroll_employees._split_name("Anvar", False) is None


def test_enroll_creates_each_person_once(tmp_path, session):
    people = tmp_path / "texniklar"
    _person(people / "Anvarov Anvar", (1, 2, 3))
    _person(people / "Karimov Sardor", (4, 5))

    assert enroll_employees.enroll(
        people, department="Texniklar", max_photos=5, given_first=False, dry_run=True
    ) == 0
    assert session.scalar(select(Employee)) is None  # dry run writes nothing

    assert enroll_employees.enroll(
        people, department="Texniklar", max_photos=5, given_first=False, dry_run=False
    ) == 0
    employees = {item.full_name: item for item in session.scalars(select(Employee)).all()}
    assert set(employees) == {"Anvar Anvarov", "Sardor Karimov"}
    assert all(item.department == "Texniklar" for item in employees.values())
    assert len(employees["Anvar Anvarov"].embeddings) == 3
    assert len(employees["Sardor Karimov"].embeddings) == 2

    # A second run must not duplicate employees or photos.
    assert enroll_employees.enroll(
        people, department="Texniklar", max_photos=5, given_first=False, dry_run=False
    ) == 0
    assert len(session.scalars(select(Employee)).all()) == 2
    session.expire_all()
    assert len(employees["Anvar Anvarov"].embeddings) == 3


def test_cross_check_flags_two_employees_with_the_same_photos(tmp_path, session):
    people = tmp_path / "texniklar"
    _person(people / "Anvarov Anvar", (7, 8))
    _person(people / "Karimov Sardor", (7, 8))  # same person enrolled twice

    enroll_employees.enroll(
        people, department="Texniklar", max_photos=5, given_first=False, dry_run=False
    )
    assert enroll_employees.cross_check(0.6) == 1


def test_cross_check_accepts_distinct_vectors(session):
    first = employee_service.create_employee(session, first_name="Ali", last_name="Valiyev")
    second = employee_service.create_employee(session, first_name="Olim", last_name="Karimov")

    from app.models import FaceEmbedding

    session.add(
        FaceEmbedding(
            employee_id=first.id,
            model_name="test",
            dimension=2,
            quality=0.9,
            source_image=f"{first.employee_code}/a.jpg",
            vector=FaceEmbedding.from_ndarray(np.array([1.0, 0.0], dtype=np.float32)),
        )
    )
    session.add(
        FaceEmbedding(
            employee_id=second.id,
            model_name="test",
            dimension=2,
            quality=0.9,
            source_image=f"{second.employee_code}/b.jpg",
            vector=FaceEmbedding.from_ndarray(np.array([0.0, 1.0], dtype=np.float32)),
        )
    )
    session.flush()

    assert enroll_employees.cross_check(0.6) == 0
