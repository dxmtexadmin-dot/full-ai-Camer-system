"""Main dashboard pages: Bosh sahifa and Jonli kamera."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.api.cameras import camera_overview
from app.database import get_db
from app.models import Camera
from app.services import asset_service, employee_service, event_service, inventory_service
from app.services.monitoring_service import get_monitor
from app.utils.time_utils import local_today

logger = logging.getLogger(__name__)
router = APIRouter()


def active_holdings(monitor) -> list[dict]:
    """Objects currently held by somebody, read from the live pipelines."""
    items: list[dict] = []
    for code, runtime in list(getattr(monitor, "runtimes", {}).items()):
        pipeline = getattr(runtime, "pipeline", None)
        analyzer = getattr(pipeline, "analyzer", None)
        if analyzer is None:
            continue
        for held in analyzer.active_possessions():
            items.append(
                {
                    "camera": code,
                    "category": held.category,
                    "employee_name": held.employee_name,
                    "since": held.started_at,
                    "held_seconds": held.held_seconds,
                }
            )
    items.sort(key=lambda item: item["since"])
    return items


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request, db: Session = Depends(get_db)):
    user = deps.require_user(request, db)
    monitor = get_monitor()
    rows = camera_overview(db)
    total_cameras = len(rows)
    online = sum(1 for row in rows if row["status"].value == "ONLINE")
    stats = event_service.dashboard_stats(db, local_today())
    balances = inventory_service.balances(db)
    issues = [item for item in balances if item.has_issue]
    recent = event_service.query_events(db, limit=12)
    pending_reviews = event_service.count_pending(db)
    return deps.render(
        request,
        "dashboard.html",
        {
            "cameras": rows,
            "total_cameras": total_cameras,
            "online_cameras": online,
            "stats": stats,
            "holdings": active_holdings(monitor),
            "pending_total": pending_reviews,
            "review_pending": pending_reviews,
            "balances": balances,
            "issues": issues,
            "recent_events": recent,
            "employees": employee_service.count(db, active_only=True),
            "yolo": monitor.yolo_service.status(),
            "face": monitor.face_service.status(),
            "sheets": monitor.sheets_service.status(),
            "monitor_started": monitor.is_started,
            "pending_sync": len(event_service.pending_sync_events(db, limit=1000)),
            "asset_stats": asset_service.stats(db),
        },
        user=user,
    )


@router.get("/live", response_class=HTMLResponse)
def live_view(request: Request, camera: Optional[str] = None, db: Session = Depends(get_db)):
    user = deps.require_user(request, db)
    rows = camera_overview(db)
    cameras = db.scalars(select(Camera).order_by(Camera.code)).all()
    selected_code = camera or (cameras[0].code if cameras else None)
    selected_row = next((row for row in rows if row["camera"].code == selected_code), None)
    recent = event_service.recent_events_for_camera(db, selected_code, limit=10) if selected_code else []
    return deps.render(
        request,
        "live.html",
        {
            "cameras": cameras,
            "rows": rows,
            "selected_code": selected_code,
            "selected_row": selected_row,
            "recent_events": recent,
        },
        user=user,
    )
