"""Employee CRUD, reference photo storage and biometric templates."""

from __future__ import annotations

import hashlib
import logging
import shutil
from pathlib import Path
from typing import Iterable, Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.models import Employee, FaceEmbedding
from app.services import audit_service
from app.services.face_service import FaceService, get_face_service
from app.utils.images import decode_image, image_quality, validate_upload
from app.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

# Bitta xodim uchun shablonlar soni cheklangan: galereya tez qoladi va
# tasodifiy/qayta yuklashlar to‘planib ketmaydi.
MAX_REFERENCE_PHOTOS = 15


def next_employee_code(session: Session) -> str:
    """EMP001, EMP002, ... based on the highest existing number."""
    codes = session.scalars(select(Employee.employee_code)).all()
    highest = 0
    for code in codes:
        digits = "".join(char for char in str(code) if char.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return f"EMP{highest + 1:03d}"


def create_employee(
    session: Session,
    *,
    first_name: str,
    last_name: str,
    department: str = "",
    employee_number: Optional[str] = None,
    notes: Optional[str] = None,
    employee_code: Optional[str] = None,
    is_active: bool = True,
    user=None,
) -> Employee:
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    if len(first_name) < 2 or len(last_name) < 2:
        raise ValueError("Ism va familiya kamida 2 ta belgidan iborat bo‘lishi kerak.")
    code = (employee_code or next_employee_code(session)).strip().upper()
    if session.scalar(select(Employee).where(Employee.employee_code == code)) is not None:
        raise ValueError(f"'{code}' kodi allaqachon mavjud.")

    employee = Employee(
        employee_code=code,
        first_name=first_name[:80],
        last_name=last_name[:80],
        department=(department or "").strip()[:120],
        employee_number=(employee_number or "").strip() or None,
        notes=(notes or "").strip() or None,
        is_active=is_active,
    )
    session.add(employee)
    session.flush()
    audit_service.record(
        session,
        action="employee.create",
        entity="employee",
        entity_id=employee.employee_code,
        details={"name": employee.full_name, "department": employee.department},
        user=user,
        username="admin",
    )
    logger.info("Xodim qo‘shildi: %s %s", employee.employee_code, employee.full_name)
    return employee


def update_employee(session: Session, employee: Employee, *, user=None, **fields) -> Employee:
    for field in ("first_name", "last_name", "department", "employee_number", "notes"):
        if field in fields and fields[field] is not None:
            setattr(employee, field, str(fields[field]).strip()[:500])
    if "is_active" in fields and fields["is_active"] is not None:
        employee.is_active = bool(fields["is_active"])
    employee.updated_at = utc_now()
    session.flush()
    audit_service.record(
        session,
        action="employee.update",
        entity="employee",
        entity_id=employee.employee_code,
        details={key: str(value) for key, value in fields.items() if value is not None},
        user=user,
        username="admin",
    )
    return employee


def reference_dir(employee: Employee, settings: Optional[Settings] = None) -> Path:
    settings = settings or get_settings()
    path = settings.employees_dir / employee.employee_code
    path.mkdir(parents=True, exist_ok=True)
    return path


def _next_reference_path(employee: Employee, suffix: str, settings: Settings) -> Path:
    folder = reference_dir(employee, settings)
    # Fayl nomi soni bo‘yicha eng kattasidan keyingisini olamiz: rasmlar
    # o‘chirilganda bo‘shliq paydo bo‘ladi va oddiy sanash mavjud faylni
    # ustiga yozib yuborishi mumkin edi.
    highest = 0
    for path in folder.glob("reference_*"):
        digits = "".join(char for char in path.stem if char.isdigit())
        if digits:
            highest = max(highest, int(digits))
    return folder / f"reference_{highest + 1:02d}{suffix or '.jpg'}"


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _stored_digests(folder: Path) -> set[str]:
    """Content hashes of the photos already stored for one employee."""
    digests: set[str] = set()
    for path in folder.glob("reference_*"):
        try:
            digests.add(_digest(path.read_bytes()))
        except OSError:  # pragma: no cover - permissions
            continue
    return digests


def add_reference_images(
    session: Session,
    employee: Employee,
    uploads: Iterable[tuple[str, bytes]],
    *,
    face_service: Optional[FaceService] = None,
    user=None,
    settings: Optional[Settings] = None,
) -> tuple[int, list[str], list[str]]:
    """Store photos and create embeddings.

    Returns ``(stored, errors, warnings)``. A photo that cannot produce an
    embedding is still saved but reported, so an operator can re-shoot it.
    """
    settings = settings or get_settings()
    service = face_service or get_face_service(settings)
    stored = 0
    errors: list[str] = []
    warnings: list[str] = []

    if not service.available:
        return 0, [f"Yuz modeli mavjud emas: {service.reason_unavailable}"], warnings

    # Brauzer bir formani qayta yuborishi mumkin (F5, sekin tarmoq), shuning
    # uchun bir xil rasm ikkinchi marta saqlanmaydi.
    existing = int(
        session.scalar(
            select(func.count(FaceEmbedding.id)).where(FaceEmbedding.employee_id == employee.id)
        )
        or 0
    )
    seen = _stored_digests(reference_dir(employee, settings))

    for filename, data in uploads:
        problem = validate_upload(filename, data)
        if problem:
            errors.append(f"{filename}: {problem}")
            continue
        digest = _digest(data)
        if digest in seen:
            warnings.append(f"{filename}: bu rasm avval yuklangan, qayta saqlanmadi.")
            continue
        if existing + stored >= MAX_REFERENCE_PHOTOS:
            errors.append(
                f"Chegara: bitta xodim uchun {MAX_REFERENCE_PHOTOS} tagacha rasm saqlanadi. "
                f"Ortiqcha rasmlarni o‘chirib, keyin qayta yuklang."
            )
            break
        image = decode_image(data)
        if image is None:
            errors.append(f"{filename}: rasm o‘qilmadi.")
            continue

        result = service.create_embedding(image)
        if not result.ok:
            errors.append(f"{filename}: " + " ".join(result.warnings or ["embedding yaratilmadi"]))
            continue
        warnings.extend(f"{filename}: {item}" for item in result.warnings)

        suffix = Path(filename).suffix.lower() or ".jpg"
        target = _next_reference_path(employee, suffix, settings)
        target.write_bytes(data)
        seen.add(digest)

        session.add(
            FaceEmbedding(
                employee_id=employee.id,
                model_name=settings.face_model_name,
                dimension=int(result.embedding.shape[0]),
                quality=result.quality,
                source_image=f"{employee.employee_code}/{target.name}",
                vector=FaceEmbedding.from_ndarray(result.embedding),
            )
        )
        stored += 1

    session.flush()
    if stored:
        audit_service.record(
            session,
            action="employee.reference_added",
            entity="employee",
            entity_id=employee.employee_code,
            details={"stored": stored, "warnings": len(warnings)},
            user=user,
            username="admin",
        )
        # The dashboard and pipeline must see the new templates immediately.
        service.gallery.reload(session)
    return stored, errors, warnings


def delete_reference(
    session: Session,
    employee: Employee,
    embedding_id: int,
    *,
    user=None,
    settings: Optional[Settings] = None,
) -> bool:
    settings = settings or get_settings()
    embedding = session.get(FaceEmbedding, embedding_id)
    if embedding is None or embedding.employee_id != employee.id:
        return False
    source = embedding.source_image
    path = settings.employees_dir / source
    try:
        if path.exists():
            path.unlink()
    except OSError as exc:  # pragma: no cover - permissions
        logger.warning("Rasmni o‘chirib bo‘lmadi (%s): %s", path, exc)
    session.delete(embedding)
    session.flush()
    # Biometrik ma’lumot o‘chirilishi ham audit jurnalida qolishi shart.
    audit_service.record(
        session,
        action="employee.reference_deleted",
        entity="employee",
        entity_id=employee.employee_code,
        details={"source_image": source, "embedding_id": embedding_id},
        user=user,
        username="admin",
    )
    return True


def delete_employee(
    session: Session,
    employee: Employee,
    *,
    user=None,
    settings: Optional[Settings] = None,
    remove_photos: bool = True,
) -> None:
    """Remove the employee and all biometric data (retention policy)."""
    settings = settings or get_settings()
    folder = settings.employees_dir / employee.employee_code
    code = employee.employee_code
    name = employee.full_name
    # Embeddings cascade with the employee row (ORM cascade + FK ON DELETE CASCADE).
    session.delete(employee)
    session.flush()
    if remove_photos and folder.exists():
        try:
            shutil.rmtree(folder)
        except OSError as exc:  # pragma: no cover - permissions
            logger.warning("Papkani o‘chirib bo‘lmadi (%s): %s", folder, exc)
    audit_service.record(
        session,
        action="employee.delete",
        entity="employee",
        entity_id=code,
        details={"name": name, "biometric_deleted": True},
        user=user,
        username="admin",
    )
    logger.warning("Xodim va biometrik ma’lumotlari o‘chirildi: %s", code)


def set_active(session: Session, employee: Employee, active: bool, *, user=None) -> Employee:
    employee.is_active = bool(active)
    employee.updated_at = utc_now()
    session.flush()
    audit_service.record(
        session,
        action="employee.activate" if active else "employee.deactivate",
        entity="employee",
        entity_id=employee.employee_code,
        user=user,
        username="admin",
    )
    return employee


def search(
    session: Session,
    query: str = "",
    *,
    active_only: bool = False,
    limit: int = 200,
) -> list[Employee]:
    stmt = select(Employee).order_by(Employee.employee_code)
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                Employee.first_name.ilike(like),
                Employee.last_name.ilike(like),
                Employee.employee_code.ilike(like),
                Employee.department.ilike(like),
                Employee.employee_number.ilike(like),
            )
        )
    if active_only:
        stmt = stmt.where(Employee.is_active.is_(True))
    return list(session.scalars(stmt.limit(limit)).all())


def get_by_code(session: Session, code: str) -> Optional[Employee]:
    return session.scalar(select(Employee).where(Employee.employee_code == code.strip().upper()))


def count(session: Session, *, active_only: bool = False) -> int:
    stmt = select(func.count(Employee.id))
    if active_only:
        stmt = stmt.where(Employee.is_active.is_(True))
    return int(session.scalar(stmt) or 0)


def quality_summary(employee: Employee) -> str:
    if not employee.embeddings:
        return "Biometrik shablon yo‘q"
    best = max(item.quality for item in employee.embeddings)
    return f"{len(employee.embeddings)} rasm, sifat {best:.2f}"
