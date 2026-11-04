"""Movement history, human review workflow and evidence access."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import FileResponse, HTMLResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import deps
from app.database import get_db
from app.models import Event
from app.models.enums import EventType, VerificationStatus
from app.services import employee_service, equipment_service, event_service, inventory_service
from app.utils.time_utils import local_today

logger = logging.getLogger(__name__)
router = APIRouter()


def _event_filters(request: Request) -> dict:
    params = request.query_params
    start, end = deps.safe_range(params.get("start"), params.get("end"))
    employee_id = params.get("employee_id")
    return {
        "start": start,
        "end": end,
        "camera_code": params.get("camera") or None,
        "status": params.get("status") or None,
        "event_types": [params["type"]] if params.get("type") else None,
        "category": params.get("category") or None,
        "employee_id": int(employee_id) if employee_id and employee_id.isdigit() else None,
        "search": params.get("q") or None,
    }


@router.get("/movements", response_class=HTMLResponse)
def movements_page(request: Request, page: int = 1, db: Session = Depends(get_db)):
    user = deps.require_user(request, db)
    page = max(1, page)
    per_page = 40
    filters = _event_filters(request)
    total = event_service.count_events(db, **filters)
    events = event_service.query_events(db, limit=per_page, offset=(page - 1) * per_page, **filters)
    categories = equipment_service.categories(db)
    cameras = sorted({code for (code,) in db.execute(select(Event.camera_code).distinct()).all() if code})
    employees = employee_service.search(db, active_only=False, limit=500)
    return deps.render(
        request,
        "movements.html",
        {
            "events": events,
            "total": total,
            "page": page,
            "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page),
            "categories": categories,
            "cameras": cameras,
            "employees": employees,
            "event_types": list(EventType),
            "statuses": list(VerificationStatus),
            "filters": request.query_params,
            "today": local_today(),
        },
        user=user,
    )


@router.get("/review", response_class=HTMLResponse)
def review_page(request: Request, db: Session = Depends(get_db)):
    user = deps.require_operator(request, db)
    pending = event_service.pending_reviews(db, limit=100)
    balances = [item for item in inventory_service.balances(db) if item.has_issue]
    pending_reviews = event_service.count_pending(db)
    return deps.render(
        request,
        "review.html",
        {
            "events": pending,
            "pending_total": pending_reviews,
            "review_pending": pending_reviews,
            "balances": balances,
            "employees": employee_service.search(db, active_only=True, limit=500),
            "categories": equipment_service.categories(db),
        },
        user=user,
    )


@router.post("/api/events/{event_id}/review")
def review_event_action(
    request: Request,
    event_id: int,
    decision: str = Form(...),
    reason: str = Form(""),
    employee_id: Optional[str] = Form(None),
    asset_id: Optional[str] = Form(None),
    category_code: Optional[str] = Form(None),
    next_url: str = Form("/review"),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    operator = deps.require_operator(request, db)
    deps.verify_csrf(request, csrf_token)
    event = db.get(Event, event_id)
    if event is None:
        deps.flash(request, "Hodisa topilmadi.", "error")
        return deps.redirect("/review")

    try:
        employee_id_value = deps.form_int(employee_id, "Xodim ID")
        asset_id_value = deps.form_int(asset_id, "Jihoz ID")
        event_service.review_event(
            db,
            event,
            decision=decision,
            user=operator,
            reason=reason,
            employee_id=employee_id_value,
            asset_id=asset_id_value,
            equipment_category=category_code or None,
        )
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect(next_url or "/review")
    except IntegrityError:
        db.rollback()
        logger.exception("Tekshirishni saqlab bo‘lmadi: %s", event.public_id)
        deps.flash(request, "Bu hodisani saqlab bo‘lmadi — ma’lumot cheklovi. Sahifani yangilab, qayta urinib ko‘ring.", "error")
        return deps.redirect(next_url or "/review")

    db.commit()
    label = "tasdiqlandi" if decision.upper() == "CONFIRMED" else "rad etildi"
    deps.flash(request, f"{event.public_id} {label}. Jurnal yangilandi.")
    return deps.redirect(next_url or "/review")


@router.get("/api/events/{public_id}/evidence")
def event_evidence(request: Request, public_id: str, db: Session = Depends(get_db)):
    deps.require_operator(request, db)
    event = db.scalar(select(Event).where(Event.public_id == public_id))
    if event is None or not event.evidence_path:
        return HTMLResponse("Dalil rasmi yo‘q", status_code=404)
    try:
        path = deps.evidence_file(event.evidence_path)
    except ValueError:
        return HTMLResponse("Ruxsat yo‘q", status_code=403)
    if not path.exists():
        return HTMLResponse("Fayl topilmadi (muddati o‘tgan bo‘lishi mumkin)", status_code=404)
    return FileResponse(path, headers={"Cache-Control": "private, max-age=30"})


@router.get("/api/events")
def events_json(request: Request, limit: int = 50, db: Session = Depends(get_db)):
    """Minimal read-only JSON API for integrations (no biometric data)."""
    deps.require_user(request, db)
    filters = _event_filters(request)
    events = event_service.query_events(db, limit=min(200, max(1, limit)), **filters)
    return {
        "count": len(events),
        "items": [
            {
                "event_id": event.public_id,
                "type": event.event_type,
                "occurred_at_utc": event.occurred_at.isoformat(),
                "camera": event.camera_code,
                "employee_code": event.employee.employee_code if event.employee else None,
                "equipment_category": event.equipment_category,
                "asset_code": event.asset.asset_code if event.asset else None,
                "direction": event.direction,
                "verification_status": event.verification_status,
                "detection_confidence": event.detection_confidence,
                "face_match_score": event.face_match_score,
                "association_confidence": event.association_confidence,
            }
            for event in events
        ],
    }


@router.get("/api/employees/search")
def search_employees(request: Request, q: str = "", db: Session = Depends(get_db)):
    deps.require_operator(request, db)
    employees = employee_service.search(db, q, active_only=True, limit=20)
    return {"items": [{"id": item.id, "code": item.employee_code, "name": item.full_name} for item in employees]}


@router.get("/api/assets/search")
def search_assets(request: Request, q: str = "", category: str = "", db: Session = Depends(get_db)):
    deps.require_operator(request, db)
    assets = equipment_service.search_assets(db, q, category_code=category or None, limit=20)
    return {
        "items": [
            {"id": item.id, "code": item.asset_code, "category": item.category.code if item.category else ""} for item in assets
        ]
    }
