"""Reports, CSV export and manual Google Sheets synchronisation."""

from __future__ import annotations

import csv
import io
import logging
from collections import Counter
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.database import get_db
from app.models import Event, SyncLog
from app.models.enums import BUSINESS_EVENT_TYPES, EventType, VerificationStatus
from app.services import employee_service, equipment_service, event_service, inventory_service
from app.services.google_sheets_service import get_sheets_service
from app.utils.time_utils import format_local, local_today

logger = logging.getLogger(__name__)
router = APIRouter()


def _filters(request: Request) -> dict:
    params = request.query_params
    start, end = deps.safe_range(params.get("start"), params.get("end"))
    employee_id = params.get("employee_id")
    return {
        "start": start,
        "end": end,
        "camera_code": params.get("camera") or None,
        "category": params.get("category") or None,
        "status": params.get("status") or None,
        "event_types": [params["type"]] if params.get("type") else None,
        "employee_id": int(employee_id) if employee_id and employee_id.isdigit() else None,
    }


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request, db: Session = Depends(get_db)):
    user = deps.require_user(request, db)
    filters = _filters(request)
    if not filters.get("event_types"):
        # This card is about people and equipment. Camera connect/disconnect
        # chatter would otherwise push every real event out of the first 200 rows.
        filters["event_types"] = [item.value for item in BUSINESS_EVENT_TYPES]
    events = event_service.query_events(db, limit=2000, **filters)

    by_type = Counter(event.event_type for event in events)
    by_category = Counter(event.equipment_category for event in events if event.equipment_category)
    by_employee = Counter(event.employee_label for event in events)
    by_status = Counter(event.verification_status for event in events)

    sheets = get_sheets_service()
    last_log = db.scalars(select(SyncLog).order_by(SyncLog.started_at.desc()).limit(5)).all()
    unsynced = len(event_service.pending_sync_events(db, limit=5000))
    cameras = sorted({code for (code,) in db.execute(select(Event.camera_code).distinct()).all() if code})

    return deps.render(
        request,
        "reports.html",
        {
            "events": events[:200],
            "event_total": len(events),
            "by_type": [
                {"code": code, "label": EventType(code).label_uz if code in EventType._value2member_map_ else code, "count": count}
                for code, count in by_type.most_common()
            ],
            "by_category": [
                {"code": code, "label": next((item.label_uz for item in equipment_service.categories(db) if item.code == code), code), "count": count}
                for code, count in by_category.most_common()
            ],
            "by_employee": [{"name": name, "count": count} for name, count in by_employee.most_common(15)],
            "by_status": [
                {"code": code, "label": VerificationStatus(code).label_uz if code in VerificationStatus._value2member_map_ else code, "count": count}
                for code, count in by_status.most_common()
            ],
            "balances": inventory_service.balances(db),
            "categories": equipment_service.categories(db),
            "cameras": cameras,
            "employees": employee_service.search(db, active_only=False, limit=500),
            "event_types": list(EventType),
            "statuses": list(VerificationStatus),
            "filters": request.query_params,
            "sheets": sheets.status(),
            "sync_logs": last_log,
            "unsynced": unsynced,
            "today": local_today(),
        },
        user=user,
    )


@router.get("/reports/export.csv")
def export_csv(request: Request, db: Session = Depends(get_db)):
    deps.require_user(request, db)
    filters = _filters(request)
    events = event_service.query_events(db, limit=20000, **filters)

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        [
            "Event ID",
            "Vaqt (mahalliy)",
            "Hodisa turi",
            "Kamera",
            "Xodim kodi",
            "Xodim",
            "Jihoz toifasi",
            "Asset ID",
            "Yo‘nalish",
            "Tasdiqlash holati",
            "Aniqlik",
            "Yuz o‘xshashligi",
            "Bog‘lanish ishonchi",
            "Izoh",
        ]
    )
    for event in events:
        writer.writerow(
            [
                event.public_id,
                format_local(event.occurred_at),
                event.event_type,
                event.camera_code,
                event.employee.employee_code if event.employee else "",
                event.employee_label,
                event.equipment_category or "",
                event.asset.asset_code if event.asset else "",
                event.direction,
                event.verification_status,
                f"{event.detection_confidence:.3f}" if event.detection_confidence is not None else "",
                f"{event.face_match_score:.3f}" if event.face_match_score is not None else "",
                f"{event.association_confidence:.3f}" if event.association_confidence is not None else "",
                (event.notes or "").replace("\n", " "),
            ]
        )

    payload = "\ufeff" + buffer.getvalue()  # BOM so Excel shows Uzbek text correctly
    filename = f"hodisalar_{local_today().isoformat()}.csv"
    return StreamingResponse(
        io.BytesIO(payload.encode("utf-8")),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/api/sheets/sync")
def manual_sync(
    request: Request,
    csrf_token: Optional[str] = Form(None),
    next_url: Optional[str] = Form(None, alias="next"),
    db: Session = Depends(get_db),
):
    user = deps.require_operator(request, db)
    deps.verify_csrf(request, csrf_token)
    result = get_sheets_service().sync(db, triggered_by=f"manual:{user.username}")
    db.commit()
    deps.flash(request, result.summary, "ok" if result.ok else "error")
    target = "/reports#sheets"
    if next_url and next_url.startswith("/") and not next_url.startswith("//"):
        target = next_url
    return deps.redirect(target)


@router.get("/api/sheets/status")
def sheets_status(request: Request, db: Session = Depends(get_db)):
    deps.require_user(request, db)
    service = get_sheets_service()
    last = db.scalars(select(SyncLog).order_by(SyncLog.started_at.desc()).limit(1)).first()
    return {
        **service.status(),
        "pending_events": len(event_service.pending_sync_events(db, limit=5000)),
        "last_log": {
            "started_at": format_local(last.started_at) if last else None,
            "status": last.status if last else None,
            "message": last.message if last else None,
        },
    }
