"""Sozlamalar (settings) page: model/threshold configuration, users, audit."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.database import get_db
from app.models import SyncLog, User
from app.services import audit_service, settings_service
from app.services.monitoring_service import get_monitor

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, db: Session = Depends(get_db)):
    user = deps.require_admin(request, db)
    monitor = get_monitor()
    settings = deps.get_settings()

    fields_by_group: dict[str, list] = {}
    for field in settings_service.FIELDS:
        fields_by_group.setdefault(field.group, []).append(field)

    return deps.render(
        request,
        "settings.html",
        {
            "fields_by_group": fields_by_group,
            "values": settings_service.current_values(settings),
            "warnings": settings.warnings(),
            "yolo": monitor.yolo_service.status(),
            "face": monitor.face_service.status(),
            "sheets": monitor.sheets_service.status(),
            "monitor_started": monitor.is_started,
            "users": db.scalars(select(User).order_by(User.username)).all(),
            "audit": audit_service.recent(db, limit=40),
            "sync_logs": db.scalars(select(SyncLog).order_by(SyncLog.started_at.desc()).limit(5)).all(),
            "env_path": str(settings_service.ENV_FILE),
            "settings": settings,
        },
        user=user,
    )


@router.post("/api/settings")
async def save_settings(request: Request, csrf_token: Optional[str] = Form(None), db: Session = Depends(get_db)):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    form = await request.form()

    values: dict[str, str] = {}
    for field in settings_service.FIELDS:
        if field.kind == "bool":
            values[field.key] = "true" if form.get(field.key) else "false"
        elif field.key in form:
            values[field.key] = str(form.get(field.key) or "")

    try:
        changed = settings_service.update_env(values)
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/settings")

    if changed:
        settings_service.refresh_runtime(get_monitor())
        audit_service.record(
            db,
            action="settings.update",
            entity="settings",
            entity_id="env",
            details={"changed": changed},
            user=admin,
        )
        db.commit()
        deps.flash(request, f"Sozlamalar saqlandi va qo‘llanildi: {', '.join(changed)}")
    else:
        deps.flash(request, "O‘zgarish yo‘q.")
    return deps.redirect("/settings")


@router.post("/api/model/reload")
def reload_model(request: Request, csrf_token: Optional[str] = Form(None), db: Session = Depends(get_db)):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    monitor = get_monitor()
    monitor.refresh_detection_rules()
    ok = monitor.yolo_service.reload()
    monitor.refresh_gallery()
    audit_service.record(db, action="model.reload", entity="yolo", details={"ok": ok}, user=admin)
    db.commit()
    deps.flash(
        request,
        "Model qayta yuklandi." if ok else f"Model yuklanmadi: {monitor.yolo_service.reason_unavailable}",
        "ok" if ok else "error",
    )
    return deps.redirect("/settings")


@router.post("/api/gallery/reload")
def reload_gallery(request: Request, csrf_token: Optional[str] = Form(None), db: Session = Depends(get_db)):
    user = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    count = get_monitor().refresh_gallery()
    deps.flash(request, f"Yuz galereyasi yangilandi: {count} shablon.")
    return deps.redirect("/settings")
