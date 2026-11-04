"""Employee management: page, forms, photo upload and restricted photo access."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.database import get_db
from app.models import Camera, Employee, FaceEmbedding
from app.services import employee_service
from app.services.face_service import FaceUnavailableError, get_face_service
from app.services.monitoring_service import get_monitor
from app.utils.images import encode_jpeg, ensure_within, padded_crop

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/employees", response_class=HTMLResponse)
def employees_page(request: Request, q: str = "", active: str = "all", db: Session = Depends(get_db)):
    user = deps.require_admin(request, db)
    active_only = active == "active"
    employees = employee_service.search(db, q, active_only=active_only)
    face = get_face_service()
    return deps.render(
        request,
        "employees.html",
        {
            "employees": employees,
            "query": q,
            "active_filter": active,
            "face_status": face.status(),
            "next_code": employee_service.next_employee_code(db),
        },
        user=user,
    )


@router.get("/employees/{code}", response_class=HTMLResponse)
def employee_detail(request: Request, code: str, db: Session = Depends(get_db)):
    user = deps.require_admin(request, db)
    employee = employee_service.get_by_code(db, code)
    if employee is None:
        deps.flash(request, "Xodim topilmadi.", "error")
        return deps.redirect("/employees")
    face = get_face_service()
    cameras = db.scalars(select(Camera).where(Camera.is_enabled.is_(True)).order_by(Camera.code)).all()
    return deps.render(
        request,
        "employee_detail.html",
        {"employee": employee, "face_status": face.status(), "cameras": cameras},
        user=user,
    )


@router.post("/api/employees")
def create_employee(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(...),
    department: str = Form(""),
    employee_number: Optional[str] = Form(None),
    employee_code: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    try:
        employee = employee_service.create_employee(
            db,
            first_name=first_name,
            last_name=last_name,
            department=department,
            employee_number=employee_number,
            employee_code=employee_code,
            notes=notes,
            user=admin,
        )
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/employees")
    db.commit()
    deps.flash(
        request,
        f"Xodim qo‘shildi: {employee.employee_code}. Endi kamida 2 ta aniq rasm yuklang.",
    )
    return deps.redirect(f"/employees/{employee.employee_code}")


@router.post("/api/employees/{employee_id}/update")
def update_employee(
    request: Request,
    employee_id: int,
    first_name: str = Form(...),
    last_name: str = Form(...),
    department: str = Form(""),
    employee_number: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    employee = db.get(Employee, employee_id)
    if employee is None:
        deps.flash(request, "Xodim topilmadi.", "error")
        return deps.redirect("/employees")
    employee_service.update_employee(
        db,
        employee,
        user=admin,
        first_name=first_name,
        last_name=last_name,
        department=department,
        employee_number=employee_number,
        notes=notes,
    )
    db.commit()
    deps.flash(request, "Xodim ma’lumotlari yangilandi.")
    return deps.redirect(f"/employees/{employee.employee_code}")


@router.post("/api/employees/{employee_id}/toggle")
def toggle_employee(
    request: Request,
    employee_id: int,
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    employee = db.get(Employee, employee_id)
    if employee is None:
        deps.flash(request, "Xodim topilmadi.", "error")
        return deps.redirect("/employees")
    employee_service.set_active(db, employee, not employee.is_active, user=admin)
    db.commit()
    get_monitor().refresh_gallery()
    deps.flash(request, f"{employee.full_name}: {'faollashtirildi' if employee.is_active else 'bloklandi'}.")
    return deps.redirect(f"/employees/{employee.employee_code}")


@router.post("/api/employees/{employee_id}/photos")
async def upload_photos(
    request: Request,
    employee_id: int,
    photos: Optional[list[UploadFile]] = File(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    employee = db.get(Employee, employee_id)
    if employee is None:
        deps.flash(request, "Xodim topilmadi.", "error")
        return deps.redirect("/employees")

    uploads: list[tuple[str, bytes]] = []
    for photo in photos or []:
        if photo is None or not photo.filename:
            continue
        uploads.append((photo.filename, await photo.read()))

    if not uploads:
        deps.flash(request, "Rasm tanlanmadi.", "error")
        return deps.redirect(f"/employees/{employee.employee_code}")

    stored, errors, warnings = employee_service.add_reference_images(db, employee, uploads, user=admin)
    db.commit()
    get_monitor().refresh_gallery()

    if stored:
        deps.flash(request, f"{stored} ta rasm saqlandi va shablon yaratildi.")
    for warning in warnings[:3]:
        deps.flash(request, f"Ogohlantirish: {warning}", "warn")
    for error in errors[:5]:
        deps.flash(request, f"Xatolik: {error}", "error")
    return deps.redirect(f"/employees/{employee.employee_code}")


@router.post("/api/employees/{employee_id}/photos/capture")
def capture_photo(
    request: Request,
    employee_id: int,
    camera: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Take a photo from the live camera frame and enrol it as a template.

    Photos shot by the same camera (angle, lens, light) match much better than
    scanned ID photos, so this is the quickest way to raise the match score.
    """
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    employee = db.get(Employee, employee_id)
    if employee is None:
        deps.flash(request, "Xodim topilmadi.", "error")
        return deps.redirect("/employees")
    back = f"/employees/{employee.employee_code}"

    face = get_face_service()
    if not face.available:
        deps.flash(request, f"Yuz modeli ishlamayapti: {face.reason_unavailable}", "error")
        return deps.redirect(back)

    worker = get_monitor().get_worker(camera.strip())
    frame = worker.latest_frame() if worker is not None else None
    if frame is None:
        deps.flash(request, "Kameradan kadr olinmadi. Kamera yoqilganini tekshiring.", "error")
        return deps.redirect(back)

    try:
        faces = face.detect_faces(frame)
    except FaceUnavailableError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect(back)
    if not faces:
        deps.flash(request, "Kadrda yuz topilmadi. Xodim kameraga qarab tursin.", "error")
        return deps.redirect(back)

    best = max(faces, key=lambda item: item.bbox.area)
    crop = padded_crop(frame, best.bbox)
    if crop is None:
        deps.flash(request, "Yuz juda kichik - kameraga yaqinroq turing.", "error")
        return deps.redirect(back)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    uploads = [(f"kamera_{stamp}.jpg", encode_jpeg(crop, 92))]
    stored, errors, warnings = employee_service.add_reference_images(db, employee, uploads, user=admin)
    db.commit()
    get_monitor().refresh_gallery()

    if len(faces) > 1:
        warnings.insert(0, f"Kadrda {len(faces)} ta yuz bor - eng kattasi olindi.")
    if stored:
        deps.flash(request, "Kameradan rasm olindi va shablon yaratildi.")
    for warning in warnings[:3]:
        deps.flash(request, f"Ogohlantirish: {warning}", "warn")
    for error in errors[:3]:
        deps.flash(request, f"Xatolik: {error}", "error")
    return deps.redirect(back)


@router.post("/api/employees/{employee_id}/photos/{embedding_id}/delete")
def delete_photo(
    request: Request,
    employee_id: int,
    embedding_id: int,
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    employee = db.get(Employee, employee_id)
    if employee is None:
        deps.flash(request, "Xodim topilmadi.", "error")
        return deps.redirect("/employees")
    if employee_service.delete_reference(db, employee, embedding_id, user=admin):
        db.commit()
        get_monitor().refresh_gallery()
        deps.flash(request, "Rasm va shablon o‘chirildi.")
    else:
        deps.flash(request, "Rasm topilmadi.", "error")
    return deps.redirect(f"/employees/{employee.employee_code}")


@router.post("/api/employees/{employee_id}/delete")
def delete_employee(
    request: Request,
    employee_id: int,
    confirm_code: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    employee = db.get(Employee, employee_id)
    if employee is None:
        deps.flash(request, "Xodim topilmadi.", "error")
        return deps.redirect("/employees")
    if confirm_code.strip().upper() != employee.employee_code:
        deps.flash(request, "Tasdiqlash kodi mos kelmadi. Xodim o‘chirilmadi.", "error")
        return deps.redirect(f"/employees/{employee.employee_code}")
    code = employee.employee_code
    employee_service.delete_employee(db, employee, user=admin)
    db.commit()
    get_monitor().refresh_gallery()
    deps.flash(request, f"Xodim va biometrik ma’lumotlari o‘chirildi: {code}")
    return deps.redirect("/employees")


# ---------------------------------------------------------------------------
# Restricted reference photo access
# ---------------------------------------------------------------------------
@router.get("/api/employees/{code}/photos/{filename}")
def employee_photo(request: Request, code: str, filename: str, db: Session = Depends(get_db)):
    deps.require_operator(request, db)
    employee = employee_service.get_by_code(db, code)
    if employee is None:
        return deps.redirect("/employees")

    settings = deps.get_settings()
    try:
        target = ensure_within(settings.employees_dir / employee.employee_code, settings.employees_dir / employee.employee_code / filename)
    except ValueError:
        return HTMLResponse("Ruxsat yo‘q", status_code=403)

    # Only files that are actually referenced by a stored embedding.
    known = db.scalar(
        select(FaceEmbedding).where(
            FaceEmbedding.employee_id == employee.id,
            FaceEmbedding.source_image == f"{employee.employee_code}/{filename}",
        )
    )
    if known is None or not target.exists():
        return HTMLResponse("Rasm topilmadi", status_code=404)
    return FileResponse(target, headers={"Cache-Control": "private, max-age=60"})
