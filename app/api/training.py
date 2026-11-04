"""Admin section "Namuna rasmlar": teach the model with real frames.

The generic COCO model confuses small desk objects (a white mouse in a hand has
been reported as a smartphone at 0.81). Accuracy cannot be raised by a slider -
the model has to see the real thing. This page collects 10-15 pictures per
category, keeps them on disk (``data/samples/<code>/``) and hands them to the
documented training workflow.

Only administrators can open the page: the samples land in the dataset and a
retrain replaces the production model.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.config import get_settings
from app.database import get_db
from app.models import Camera
from app.services import cleanup_service, equipment_service, sample_service
from app.services.monitoring_service import get_monitor

logger = logging.getLogger(__name__)
router = APIRouter()


def _category(db: Session, code: str):
    return equipment_service.category_by_code(db, code)


def _samples_view(settings, code: str) -> list[dict]:
    rows = []
    for path in sample_service.list_samples(settings, code):
        try:
            stat = path.stat()
        except OSError:  # pragma: no cover - vanished file
            continue
        rows.append(
            {
                "name": path.name,
                "size": cleanup_service.human_bytes(stat.st_size),
                "modified": datetime.fromtimestamp(stat.st_mtime).strftime("%d.%m %H:%M"),
            }
        )
    return rows


@router.get("/training", response_class=HTMLResponse)
def training_page(request: Request, code: str = "", db: Session = Depends(get_db)):
    user = deps.require_admin(request, db)
    settings = get_settings()
    rows = sample_service.progress(db, settings)

    selected = next((row for row in rows if row.code == code), None)
    if selected is None:
        # Land on a category that is already being collected, else the first one.
        selected = next((row for row in rows if row.count), rows[0] if rows else None)

    cameras = db.scalars(
        select(Camera).where(Camera.is_enabled.is_(True)).order_by(Camera.code)
    ).all()

    return deps.render(
        request,
        "training.html",
        {
            "rows": rows,
            "totals": sample_service.totals(rows),
            "selected": selected,
            "samples": _samples_view(settings, selected.code) if selected else [],
            "cameras": cameras,
            "samples_dir": str(sample_service.samples_root(settings)),
            "target": sample_service.TARGET_PER_CATEGORY,
            "minimum": sample_service.MIN_PER_CATEGORY,
            "limit": sample_service.MAX_PER_CATEGORY,
        },
        user=user,
    )


@router.post("/api/training/{code}/samples")
async def upload_samples(
    request: Request,
    code: str,
    files: Optional[list[UploadFile]] = File(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    category = _category(db, code)
    if category is None:
        deps.flash(request, "Toifa topilmadi.", "error")
        return deps.redirect("/training")
    back = f"/training?code={category.code}"

    uploads: list[tuple[str, bytes]] = []
    for item in files or []:
        if item is None or not item.filename:
            continue
        uploads.append((item.filename, await item.read()))

    if not uploads:
        deps.flash(request, "Rasm tanlanmadi.", "error")
        return deps.redirect(back)

    result = sample_service.add_images(
        get_settings(), category.code, uploads, user=user, session=db
    )
    db.commit()

    if result.stored:
        deps.flash(
            request,
            f"«{category.label_uz}» uchun {result.stored} ta namuna saqlandi.",
        )
    if result.duplicates:
        deps.flash(request, f"{result.duplicates} ta rasm allaqachon to‘plamda bor edi.", "warn")
    for error in result.errors[:5]:
        deps.flash(request, f"Xatolik: {error}", "error")

    current = sample_service.count(get_settings(), category.code)
    if current >= sample_service.TARGET_PER_CATEGORY:
        deps.flash(request, f"To‘plam tayyor: {current} rasm. Endi modelni o‘qitish mumkin.", "ok")
    elif current >= sample_service.MIN_PER_CATEGORY:
        deps.flash(
            request,
            f"{current} rasm yig‘ildi — minimal chegaraga yetdi, yana "
            f"{sample_service.TARGET_PER_CATEGORY - current} ta qo‘shsangiz yaxshi.",
            "warn",
        )
    return deps.redirect(back)


@router.post("/api/training/{code}/capture")
def capture_sample(
    request: Request,
    code: str,
    camera: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Grab the current live frame as a teaching sample (same camera = same look)."""
    user = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    category = _category(db, code)
    if category is None:
        deps.flash(request, "Toifa topilmadi.", "error")
        return deps.redirect("/training")
    back = f"/training?code={category.code}"

    worker = get_monitor().get_worker((camera or "").strip())
    frame = worker.latest_frame() if worker is not None else None
    if frame is None:
        deps.flash(request, "Kameradan kadr olinmadi. Kamera yoqilganini tekshiring.", "error")
        return deps.redirect(back)

    try:
        stored = sample_service.add_frame(
            get_settings(), category.code, frame, user=user, session=db
        )
        db.commit()
    except sample_service.SampleError as exc:
        db.rollback()
        deps.flash(request, str(exc), "warn")
        return deps.redirect(back)

    deps.flash(request, f"Kadr saqlandi: {stored.name}")
    return deps.redirect(back)


@router.post("/api/training/{code}/samples/{name}/delete")
def delete_sample_action(
    request: Request,
    code: str,
    name: str,
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    if sample_service.delete_sample(get_settings(), code, name, user=user, session=db):
        db.commit()
        deps.flash(request, f"{name} o‘chirildi.")
    else:
        db.rollback()
        deps.flash(request, "Rasm topilmadi.", "error")
    return deps.redirect(f"/training?code={code}")


@router.get("/api/training/{code}/samples/{name}")
def sample_file(request: Request, code: str, name: str, db: Session = Depends(get_db)):
    deps.require_admin(request, db)
    try:
        path = sample_service.resolve_sample(get_settings(), code, name)
    except sample_service.SampleError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={"Cache-Control": "private, max-age=300"},
    )


@router.get("/api/training/samples.zip")
def samples_zip(
    request: Request,
    code: str = "",
    db: Session = Depends(get_db),
):
    deps.require_admin(request, db)
    payload = sample_service.zip_samples(get_settings(), code.strip() or None)
    name = f"namunalar_{code.strip()}.zip" if code.strip() else "namunalar.zip"
    return Response(
        content=payload,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
