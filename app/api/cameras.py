"""Camera configuration page, CRUD endpoints and the live MJPEG stream."""

from __future__ import annotations

import logging
import time
from typing import Iterator, Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.database import get_db
from app.models import Camera
from app.models.enums import CameraStatus, SourceType
from app.services import audit_service, overlay
from app.services.camera_service import CameraConfig
from app.services.monitoring_service import get_monitor
from app.utils.images import encode_jpeg
from app.utils.time_utils import format_local, utc_now

logger = logging.getLogger(__name__)
router = APIRouter()


def camera_overview(db: Session) -> list[dict]:
    """DB camera rows merged with the live runtime state."""
    monitor = get_monitor()
    cameras = db.scalars(select(Camera).order_by(Camera.code)).all()
    rows: list[dict] = []
    for camera in cameras:
        runtime = monitor.get_runtime(camera.code)
        rows.append(
            {
                "camera": camera,
                "running": bool(runtime and runtime.worker.is_running),
                "status": runtime.worker.status if runtime else camera.status_enum,
                "message": runtime.worker.status_message if runtime else camera.status_message,
                "frames": runtime.worker.frames_captured if runtime else 0,
                "dropped": runtime.worker.frames_dropped if runtime else 0,
                "last_frame": runtime.worker.last_frame_at if runtime else camera.last_frame_at,
                "pipeline": runtime.pipeline.health() if runtime and runtime.pipeline else None,
            }
        )
    return rows


LineCoords = tuple[Optional[int], Optional[int], Optional[int], Optional[int]]
_LINE_LABELS = ("X1", "Y1", "X2", "Y2")


def _parse_line(raw: tuple[Optional[str], Optional[str], Optional[str], Optional[str]]) -> tuple[LineCoords, Optional[str]]:
    """Blank number inputs arrive as '' - an empty line is a valid configuration."""
    values: list[Optional[int]] = []
    for label, item in zip(_LINE_LABELS, raw):
        try:
            values.append(deps.form_int(item))
        except ValueError:
            return (None, None, None, None), f"Chiziq koordinatasi {label} butun son bo‘lishi kerak."
    return (values[0], values[1], values[2], values[3]), None


def _validate_camera_form(
    *,
    code: str,
    name: str,
    source_type: str,
    source: str,
    line: LineCoords,
    confidence: Optional[float] = None,
) -> Optional[str]:
    if not code or not code.replace("-", "").replace("_", "").isalnum():
        return "Kamera kodi faqat harf, raqam, '-' va '_' dan iborat bo‘lishi kerak."
    if len(code) > 32:
        return "Kamera kodi juda uzun."
    if not name.strip():
        return "Kamera nomi kiritilishi shart."
    try:
        source_type_value = SourceType(source_type)
    except ValueError:
        return "Kamera manbasi turi noto‘g‘ri."
    if source_type_value != SourceType.SIMULATION and not source.strip():
        return "Manba (RTSP URL / qurilma raqami / fayl yo‘li) kiritilishi shart."
    if source_type_value is SourceType.RTSP and not source.strip().lower().startswith(("rtsp://", "rtsps://", "http://", "https://")):
        return "RTSP manzili rtsp:// bilan boshlanishi kerak."
    x1, y1, x2, y2 = line
    values = [x1, y1, x2, y2]
    if any(value is not None for value in values):
        if any(value is None for value in values):
            return "Chiziq uchun barcha 4 koordinata kiritilishi kerak."
        if x1 == x2 and y1 == y2:
            return "Chiziqning boshlanish va tugash nuqtalari bir xil bo‘lmasligi kerak."
    if confidence is not None and not 0.05 <= confidence <= 0.99:
        return "Ishonch chegarasi 0.05 va 0.99 oralig‘ida bo‘lishi kerak."
    return None


@router.get("/cameras", response_class=HTMLResponse)
def cameras_page(request: Request, db: Session = Depends(get_db)):
    user = deps.require_admin(request, db)
    return deps.render(request, "cameras.html", {"rows": camera_overview(db)}, user=user)


@router.post("/api/cameras")
def create_camera(
    request: Request,
    code: str = Form(...),
    name: str = Form(...),
    location: str = Form(""),
    source_type: str = Form(SourceType.RTSP.value),
    source: str = Form(""),
    is_enabled: Optional[str] = Form(None),
    line_x1: Optional[str] = Form(None),
    line_y1: Optional[str] = Form(None),
    line_x2: Optional[str] = Form(None),
    line_y2: Optional[str] = Form(None),
    flip_line: Optional[str] = Form(None),
    confidence: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    code = code.strip().upper()
    line, line_error = _parse_line((line_x1, line_y1, line_x2, line_y2))
    if line_error:
        deps.flash(request, line_error, "error")
        return deps.redirect("/cameras")
    try:
        confidence_value = deps.form_float(confidence, "Ishonch chegarasi")
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/cameras")
    problem = _validate_camera_form(
        code=code,
        name=name,
        source_type=source_type,
        source=source,
        line=line,
        confidence=confidence_value,
    )
    if problem:
        deps.flash(request, problem, "error")
        return deps.redirect("/cameras")
    if db.scalar(select(Camera).where(Camera.code == code)):
        deps.flash(request, f"'{code}' kodi bilan kamera mavjud.", "error")
        return deps.redirect("/cameras")

    camera = Camera(
        code=code,
        name=name.strip()[:120],
        location=location.strip()[:120],
        source_type=SourceType(source_type).value,
        source=source.strip(),
        is_enabled=bool(is_enabled),
        line_x1=line[0],
        line_y1=line[1],
        line_x2=line[2],
        line_y2=line[3],
        settings={"flip_line": bool(flip_line), "confidence": confidence_value},
        status=CameraStatus.DISABLED.value if not is_enabled else CameraStatus.UNKNOWN.value,
        status_message="",
    )
    db.add(camera)
    db.flush()
    audit_service.record(
        db,
        action="camera.create",
        entity="camera",
        entity_id=camera.code,
        details={"source_type": camera.source_type, "enabled": camera.is_enabled},
        user=admin,
    )
    db.commit()

    if camera.is_enabled:
        monitor = get_monitor()
        monitor.start_camera(CameraConfig.from_model(camera))
    deps.flash(request, f"Kamera qo‘shildi: {camera.code}")
    return deps.redirect("/cameras")


@router.post("/api/cameras/{camera_id}/update")
def update_camera(
    request: Request,
    camera_id: int,
    name: str = Form(...),
    location: str = Form(""),
    source_type: str = Form(...),
    source: str = Form(""),
    is_enabled: Optional[str] = Form(None),
    line_x1: Optional[str] = Form(None),
    line_y1: Optional[str] = Form(None),
    line_x2: Optional[str] = Form(None),
    line_y2: Optional[str] = Form(None),
    flip_line: Optional[str] = Form(None),
    confidence: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    camera = db.get(Camera, camera_id)
    if camera is None:
        deps.flash(request, "Kamera topilmadi.", "error")
        return deps.redirect("/cameras")

    line, line_error = _parse_line((line_x1, line_y1, line_x2, line_y2))
    if line_error:
        deps.flash(request, line_error, "error")
        return deps.redirect("/cameras")
    try:
        confidence_value = deps.form_float(confidence, "Ishonch chegarasi")
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/cameras")
    problem = _validate_camera_form(
        code=camera.code,
        name=name,
        source_type=source_type,
        source=source,
        line=line,
        confidence=confidence_value,
    )
    if problem:
        deps.flash(request, problem, "error")
        return deps.redirect("/cameras")

    monitor = get_monitor()
    monitor.stop_camera(camera.code)

    camera.name = name.strip()[:120]
    camera.location = location.strip()[:120]
    camera.source_type = SourceType(source_type).value
    camera.source = source.strip()
    camera.is_enabled = bool(is_enabled)
    camera.line_x1, camera.line_y1 = line[0], line[1]
    camera.line_x2, camera.line_y2 = line[2], line[3]
    camera.settings = {"flip_line": bool(flip_line), "confidence": confidence_value}
    camera.updated_at = utc_now()
    if not camera.is_enabled:
        camera.status = CameraStatus.DISABLED.value
        camera.status_message = "Administrator tomonidan o‘chirilgan."
    db.flush()
    audit_service.record(db, action="camera.update", entity="camera", entity_id=camera.code, user=admin)
    db.commit()

    if camera.is_enabled:
        monitor.start_camera(CameraConfig.from_model(camera))
    deps.flash(request, f"Kamera yangilandi: {camera.code}")
    return deps.redirect("/cameras")


@router.post("/api/cameras/{camera_id}/toggle")
def toggle_camera(
    request: Request,
    camera_id: int,
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    camera = db.get(Camera, camera_id)
    if camera is None:
        deps.flash(request, "Kamera topilmadi.", "error")
        return deps.redirect("/cameras")

    monitor = get_monitor()
    camera.is_enabled = not camera.is_enabled
    camera.status = CameraStatus.UNKNOWN.value if camera.is_enabled else CameraStatus.DISABLED.value
    db.flush()
    audit_service.record(
        db, action="camera.toggle", entity="camera", entity_id=camera.code, details={"enabled": camera.is_enabled}, user=admin
    )
    db.commit()

    if camera.is_enabled:
        monitor.start_camera(CameraConfig.from_model(camera))
    else:
        monitor.stop_camera(camera.code)
    deps.flash(request, f"{camera.code}: {'yoqildi' if camera.is_enabled else 'o‘chirildi'}.")
    return deps.redirect("/cameras")


@router.post("/api/cameras/{camera_id}/delete")
def delete_camera(
    request: Request,
    camera_id: int,
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    camera = db.get(Camera, camera_id)
    if camera is None:
        deps.flash(request, "Kamera topilmadi.", "error")
        return deps.redirect("/cameras")
    code = camera.code
    get_monitor().stop_camera(code)
    db.delete(camera)
    db.flush()
    audit_service.record(db, action="camera.delete", entity="camera", entity_id=code, user=admin)
    db.commit()
    deps.flash(request, f"Kamera o‘chirildi: {code}")
    return deps.redirect("/cameras")


# ---------------------------------------------------------------------------
# Live stream (MJPEG) and snapshot
# ---------------------------------------------------------------------------
def _mjpeg_stream(code: str, fps: int = 8) -> Iterator[bytes]:
    monitor = get_monitor()
    interval = 1.0 / max(1, fps)
    while True:
        worker = monitor.get_worker(code)
        if worker is None:
            break
        payload = worker.latest_annotated_jpeg()
        if payload is None:
            frame = overlay.placeholder_frame(message="Kadr kutilmoqda…")
            payload = encode_jpeg(frame, 70)
        yield b"--frame\r\nContent-Type: image/jpeg\r\nContent-Length: " + str(len(payload)).encode() + b"\r\n\r\n" + payload + b"\r\n"
        time.sleep(interval)


@router.get("/api/cameras/{code}/stream")
def camera_stream(request: Request, code: str, db: Session = Depends(get_db)):
    deps.require_user(request, db)
    headers = {"Cache-Control": "no-store", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        _mjpeg_stream(code),
        media_type="multipart/x-mixed-replace; boundary=frame",
        headers=headers,
    )


@router.get("/api/cameras/{code}/snapshot")
def camera_snapshot(request: Request, code: str, db: Session = Depends(get_db)):
    deps.require_user(request, db)
    worker = get_monitor().get_worker(code)
    payload = worker.latest_annotated_jpeg() if worker else None
    if payload is None:
        deps.flash(request, "Kadr hali mavjud emas.", "error")
        payload = encode_jpeg(overlay.placeholder_frame(message="Kadr yo‘q"), 70)
    return Response(content=payload, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/cameras/{code}/status")
def camera_status(request: Request, code: str, db: Session = Depends(get_db)):
    deps.require_user(request, db)
    runtime = get_monitor().get_runtime(code)
    if runtime is None:
        return {
            "code": code,
            "running": False,
            "status": CameraStatus.DISABLED.value,
            "status_label": CameraStatus.DISABLED.label_uz,
        }
    data = runtime.worker.health()
    data["status_label"] = runtime.worker.status.label_uz
    data["last_frame_at"] = format_local(runtime.worker.last_frame_at)
    if runtime.pipeline is not None:
        data.update(runtime.pipeline.health())
    return data
