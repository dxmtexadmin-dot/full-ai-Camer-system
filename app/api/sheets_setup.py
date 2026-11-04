"""Google Sheets bilan ulash (3 qadamli ustaxona).

The page owns the *connection* part of the mirror: upload the service-account
key, paste the spreadsheet link, then test/prepare/sync. Data itself still
flows through ``google_sheets_service``; nothing here touches the database
schema, and the private key never leaves ``secrets/``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import deps
from app.config import PROJECT_ROOT
from app.database import get_db
from app.models import EquipmentAsset, Employee, Event, SyncLog
from app.services import audit_service, event_service, inventory_service, settings_service
from app.services.google_sheets_service import (
    SHEET_LAYOUT,
    CredentialsError,
    get_sheets_service,
    parse_spreadsheet_id,
    spreadsheet_id_warning,
)
from app.services.monitoring_service import get_monitor

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_CREDENTIALS_BYTES = 64 * 1024
_TRUE = {"1", "true", "on", "yes"}


def _spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit" if spreadsheet_id else ""


def _relative(path: Path) -> str:
    try:
        return path.resolve().relative_to(PROJECT_ROOT).as_posix()
    except ValueError:  # key kept outside the project (e.g. C:\keys\...)
        return str(path)


def _counts(db: Session) -> dict:
    return {
        "employees": db.scalar(select(func.count(Employee.id))) or 0,
        "assets": db.scalar(select(func.count(EquipmentAsset.id))) or 0,
        "events": db.scalar(select(func.count(Event.id))) or 0,
        "categories": len(inventory_service.balances(db)),
        "unsynced": len(event_service.pending_sync_events(db, limit=5000)),
    }


@router.get("/sheets", response_class=HTMLResponse)
def sheets_page(request: Request, db: Session = Depends(get_db)):
    user = deps.require_admin(request, db)
    service = get_sheets_service()
    status = service.status()
    logs = db.scalars(select(SyncLog).order_by(SyncLog.started_at.desc()).limit(8)).all()

    return deps.render(
        request,
        "sheets.html",
        {
            "sheets": status,
            "credentials": service.credentials_info(),
            "spreadsheet_url": _spreadsheet_url(status["spreadsheet_id"]),
            "counts": _counts(db),
            "sheet_layout": [(title, len(header)) for title, header in SHEET_LAYOUT],
            "logs": logs,
            "ready": bool(
                status["enabled"]
                and status["spreadsheet_id"]
                and service.credentials_info()["client_email"]
            ),
        },
        user=user,
    )


@router.post("/api/sheets/credentials")
async def upload_credentials(
    request: Request,
    csrf_token: Optional[str] = Form(None),
    json_text: str = Form(""),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)

    payload = ""
    if file is not None and (file.filename or "").strip():
        raw = await file.read()
        if len(raw) > MAX_CREDENTIALS_BYTES:
            deps.flash(request, "Fayl juda katta — service account JSON odatda 2–3 KB bo‘ladi.", "error")
            return deps.redirect("/sheets")
        payload = raw.decode("utf-8", errors="replace")
    if not payload.strip():
        payload = json_text

    service = get_sheets_service()
    try:
        path, info = service.save_credentials(payload)
    except CredentialsError as exc:
        deps.flash(request, f"Kalit qabul qilinmadi: {exc}", "error")
        return deps.redirect("/sheets")
    except OSError as exc:
        logger.exception("Service account kalitini saqlab bo‘lmadi")
        deps.flash(request, f"Kalitni saqlab bo‘lmadi: {exc}", "error")
        return deps.redirect("/sheets")

    changed = settings_service.update_env({"GOOGLE_SERVICE_ACCOUNT_FILE": _relative(path)})
    settings_service.refresh_runtime(get_monitor())
    get_sheets_service().reset_client()

    audit_service.record(
        db,
        action="sheets.credentials",
        entity="sheets",
        entity_id="credentials",
        details={"client_email": info["client_email"], "project_id": info["project_id"], "env": changed},
        user=admin,
    )
    db.commit()
    deps.flash(
        request,
        f"Kalit saqlandi: {info['client_email']}. Endi jadvalni shu manzil bilan "
        "bo‘lishing (Ruxsat: Muharrir) va 3-qadamga o‘ting.",
    )
    return deps.redirect("/sheets")


@router.post("/api/sheets/spreadsheet")
def save_spreadsheet(
    request: Request,
    csrf_token: Optional[str] = Form(None),
    spreadsheet: str = Form(""),
    interval: str = Form(""),
    enable: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)

    try:
        spreadsheet_id = parse_spreadsheet_id(spreadsheet)
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/sheets")

    values = {"GOOGLE_SPREADSHEET_ID": spreadsheet_id}
    if (interval or "").strip():
        values["GOOGLE_SHEETS_SYNC_INTERVAL_SECONDS"] = interval.strip()
    if enable:
        values["GOOGLE_SHEETS_ENABLED"] = "true"

    try:
        changed = settings_service.update_env(values)
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/sheets")

    settings_service.refresh_runtime(get_monitor())
    get_sheets_service().reset_client()

    audit_service.record(
        db,
        action="sheets.spreadsheet",
        entity="sheets",
        entity_id=spreadsheet_id,
        details={"changed": changed, "enabled": bool(enable)},
        user=admin,
    )
    db.commit()
    deps.flash(
        request,
        f"Jadval ulandi: {spreadsheet_id}. 'Sinovdan o‘tkazish' tugmasi bilan aloqani tekshiring.",
    )
    warning = spreadsheet_id_warning(spreadsheet_id)
    if warning:
        deps.flash(request, warning, "error")
    return deps.redirect("/sheets")


@router.post("/api/sheets/test")
def test_connection(
    request: Request,
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)

    result = get_sheets_service().verify()
    message = result.get("message", "")
    if result.get("hint"):
        message = f"{message} {result['hint']}"
    if result.get("warning"):
        message = f"{message} {result['warning']}"
    if result.get("missing"):
        message += f" Yo‘q varaqlar: {', '.join(result['missing'])}."

    audit_service.record(
        db,
        action="sheets.test",
        entity="sheets",
        entity_id="connection",
        details={"ok": bool(result.get("ok")), "worksheets": result.get("worksheets", [])[:10]},
        user=admin,
    )
    db.commit()
    deps.flash(request, message, "ok" if result.get("ok") else "error")
    return deps.redirect("/sheets")


@router.post("/api/sheets/prepare")
def prepare_worksheets(
    request: Request,
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)

    try:
        outcome = get_sheets_service().prepare_worksheets()
    except Exception as exc:  # noqa: BLE001 - gspread raises many shapes
        logger.warning("Varaqlarni tayyorlab bo‘lmadi: %s", exc)
        deps.flash(request, f"Varaqlarni tayyorlab bo‘lmadi: {exc}", "error")
        return deps.redirect("/sheets")

    created = outcome["created"]
    if created:
        message = f"Yaratildi: {', '.join(created)}."
    else:
        message = "Barcha varaqlar allaqachon mavjud."
    audit_service.record(
        db,
        action="sheets.prepare",
        entity="sheets",
        entity_id="worksheets",
        details={"created": created},
        user=admin,
    )
    db.commit()
    deps.flash(request, message)
    return deps.redirect("/sheets")


@router.post("/api/sheets/toggle")
def toggle_sync(
    request: Request,
    csrf_token: Optional[str] = Form(None),
    enabled: str = Form("off"),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)

    service = get_sheets_service()
    turn_on = (enabled or "").strip().lower() in _TRUE
    if turn_on and not (service.settings.google_spreadsheet_id and service.settings.google_service_account_file):
        deps.flash(request, "Avval kalit va jadval ID’sini kiriting — keyin yoqish mumkin.", "error")
        return deps.redirect("/sheets")

    changed = settings_service.update_env({"GOOGLE_SHEETS_ENABLED": "true" if turn_on else "false"})
    settings_service.refresh_runtime(get_monitor())
    get_sheets_service().reset_client()

    audit_service.record(
        db,
        action="sheets.toggle",
        entity="sheets",
        entity_id="enabled",
        details={"enabled": turn_on, "changed": changed},
        user=admin,
    )
    db.commit()
    deps.flash(
        request,
        "Avtomatik sinxronizatsiya yoqildi." if turn_on else "Avtomatik sinxronizatsiya to‘xtatildi.",
    )
    return deps.redirect("/sheets")


@router.post("/api/sheets/forget")
def forget_credentials(
    request: Request,
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)

    removed = get_sheets_service().forget_credentials()
    settings_service.update_env(
        {"GOOGLE_SERVICE_ACCOUNT_FILE": "", "GOOGLE_SHEETS_ENABLED": "false"}
    )
    settings_service.refresh_runtime(get_monitor())
    get_sheets_service().reset_client()

    audit_service.record(
        db,
        action="sheets.forget",
        entity="sheets",
        entity_id="credentials",
        details={"file_removed": removed},
        user=admin,
    )
    db.commit()
    deps.flash(
        request,
        "Kalit o‘chirildi va sinxronizatsiya to‘xtatildi."
        if removed
        else "Kalit sozlamadan olib tashlandi (fayl loyihadan tashqarida bo‘lgani uchun o‘chirilmadi).",
    )
    return deps.redirect("/sheets")
