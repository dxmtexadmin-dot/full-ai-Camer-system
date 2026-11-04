"""Yuklab olish (downloads) page: read-only Excel/CSV exports.

Every role may download the operational data it is already allowed to see;
the audit log is admin-only, exactly like the settings page.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from sqlalchemy.orm import Session

from app.api import deps
from app.database import get_db
from app.services import audit_service, export_service

logger = logging.getLogger(__name__)
router = APIRouter()

XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
CSV_MEDIA = "text/csv; charset=utf-8"


def _visible_datasets(user) -> list[export_service.Dataset]:
    return [item for item in export_service.DATASETS if not item.admin_only or user.is_admin]


@router.get("/downloads", response_class=HTMLResponse)
def downloads_page(request: Request, db: Session = Depends(get_db)):
    user = deps.require_user(request, db)
    datasets = _visible_datasets(user)
    counts = export_service.row_counts(db, keys=[item.key for item in datasets])
    start, end = deps.safe_range(request.query_params.get("start"), request.query_params.get("end"))

    return deps.render(
        request,
        "downloads.html",
        {
            "datasets": datasets,
            "counts": counts,
            "start": request.query_params.get("start", ""),
            "end": request.query_params.get("end", ""),
            "range_active": bool(start or end),
            "total_rows": sum(counts.get(item.key, 0) for item in datasets),
        },
        user=user,
    )


@router.get("/downloads/all.xlsx")
def download_everything(request: Request, db: Session = Depends(get_db)):
    """One workbook, one sheet per dataset — the "hammasi" button."""
    user = deps.require_user(request, db)
    start, end = deps.safe_range(request.query_params.get("start"), request.query_params.get("end"))
    payload = export_service.full_workbook(db, start=start, end=end)

    audit_service.record(
        db,
        action="export.download",
        entity="export",
        entity_id="all",
        details={"format": "xlsx", "start": request.query_params.get("start"), "end": request.query_params.get("end")},
        user=user,
    )
    db.commit()
    return _file_response(payload, export_service.filename("all", "xlsx"), XLSX_MEDIA)


@router.get("/downloads/{dataset}.{fmt}")
def download_dataset(dataset: str, fmt: str, request: Request, db: Session = Depends(get_db)):
    user = deps.require_user(request, db)
    definition = export_service.get_dataset(dataset)

    if definition is None:
        return deps.render(
            request,
            "error.html",
            {"title": "Topilmadi", "message": f"'{dataset}' nomli eksport mavjud emas.", "user": user},
            user=user,
            status_code=404,
        )
    if definition.admin_only and not user.is_admin:
        raise deps.Forbidden("Bu faylni faqat administrator yuklab oladi.")

    fmt = fmt.lower()
    if fmt not in {"xlsx", "csv"}:
        return deps.render(
            request,
            "error.html",
            {"title": "Format xato", "message": "Faqat .xlsx yoki .csv yuklab olish mumkin.", "user": user},
            user=user,
            status_code=400,
        )

    start, end = deps.safe_range(request.query_params.get("start"), request.query_params.get("end"))
    _, rows = export_service.collect(db, definition.key, start=start, end=end)

    if fmt == "xlsx":
        payload = export_service.xlsx_bytes(
            definition.headers, rows, sheet_title=definition.title, subtitle=definition.title
        )
        media_type = XLSX_MEDIA
    else:
        payload = export_service.csv_bytes(definition.headers, rows)
        media_type = CSV_MEDIA

    audit_service.record(
        db,
        action="export.download",
        entity=definition.key,
        entity_id=definition.key,
        details={"format": fmt, "rows": len(rows)},
        user=user,
    )
    db.commit()
    return _file_response(payload, export_service.filename(definition.key, fmt), media_type)


def _file_response(payload: bytes, filename: str, media_type: str) -> Response:
    return Response(
        content=payload,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(payload)),
            "Cache-Control": "no-store",
        },
    )
