"""QR inventory pages: device registry, labels, assignment, history, review.

Every route checks the role on the server (``deps.require_*``) and every POST
verifies the CSRF token - the UI never hides a button as its only protection.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy.orm import Session

from app.api import deps
from app.database import get_db
from app.models import AssetMovementEvent, AssetRepairRecord, EquipmentAsset, EquipmentStatus
from app.models.enums import AssignmentKind, RepairStatus, Role
from app.services import asset_service, equipment_service, qr_service

logger = logging.getLogger(__name__)
router = APIRouter()

_IMAGE_HEADERS = {"Cache-Control": "private, max-age=0, no-store"}


def _asset_or_none(db: Session, asset_id: int) -> Optional[EquipmentAsset]:
    return asset_service.get_asset(db, asset_id)


def _base_url(request: Request) -> str:
    return str(request.base_url)


def _label_options(request: Request, *, submitted: bool) -> qr_service.LabelOptions:
    """Build label design options from the query string of a print form.

    Defaults (no ``parts_submitted`` marker) keep the standard design: QR,
    inventory number, name and model. The marker lets a user switch every
    element off without silently falling back to the defaults.
    """
    enabled = set(request.query_params.getlist("part")) if submitted else {
        "qr", "code", "name", "model"
    }
    return qr_service.LabelOptions(
        show_qr="qr" in enabled,
        show_asset_code="code" in enabled,
        show_name="name" in enabled,
        show_model="model" in enabled,
        show_responsible="resp" in enabled,
        show_org="org" in enabled,
        show_logo="logo" in enabled,
        org_name=request.query_params.get("org_name") or "DXM TEX",
        extra_text=request.query_params.get("extra_text") or "",
    )


def _size_from_query(request: Request) -> tuple[float, float]:
    return qr_service.parse_label_size(
        request.query_params.get("size"),
        request.query_params.get("w"),
        request.query_params.get("h"),
    )


def _filtered_options_from_request(request: Request) -> qr_service.LabelOptions:
    return _label_options(request, submitted="parts_submitted" in request.query_params)


def _mm(value: float) -> str:
    return f"{float(value):g}"


def single_label_css(width_mm: float, height_mm: float) -> str:
    """Page box + label size for one label (kept out of the template on purpose:
    a stylesheet with interpolated values confuses CSS parsers)."""
    size = f"{_mm(width_mm)}mm {_mm(height_mm)}mm"
    box = f"{_mm(width_mm)}mm;height:{_mm(height_mm)}mm"
    return (
        f".label-box{{width:{box}}}\n"
        f"@page{{size:{size};margin:0}}\n"
        "@media print{body{background:#fff}"
        ".toolbar,.hint{display:none !important}"
        ".stage{padding:0;display:block}"
        f".label-box{{width:{box};box-shadow:none;margin:0}}.label-box img{{height:100%}}}}\n"
    )


def a4_sheet_css(width_mm: float, height_mm: float, cols: int, margin_mm: float, gap_mm: float) -> str:
    """A4 page box plus the label grid (columns are computed server-side)."""
    return (
        f"@page{{size:A4;margin:{_mm(margin_mm)}mm}}\n"
        f".sheet{{display:grid;grid-template-columns:repeat({max(1, cols)},{_mm(width_mm)}mm);"
        f"gap:{_mm(gap_mm)}mm;align-content:start;justify-content:start}}\n"
        f".label{{width:{_mm(width_mm)}mm;height:{_mm(height_mm)}mm}}\n"
    )


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #
@router.get("/assets", response_class=HTMLResponse)
def assets_page(
    request: Request,
    q: str = "",
    category: str = "",
    room: str = "",
    department: str = "",
    status: str = "",
    employee: str = "",
    unassigned: str = "",
    page: int = 1,
    db: Session = Depends(get_db),
):
    user = deps.require_user(request, db)
    page = max(1, page)
    per_page = 50
    employee_id = deps.form_int(employee, "Xodim") if employee else None

    if user.role_enum == Role.TECHNICIAN:
        # A technician only sees the devices they service.
        assets = asset_service.technician_assets(db, user)
        if q:
            needle = q.strip().lower()
            assets = [
                item
                for item in assets
                if needle in item.asset_code.lower()
                or needle in (item.name or "").lower()
                or needle in (item.responsible_name or "").lower()
            ]
        total = len(assets)
    else:
        assets = asset_service.filtered_assets(
            db,
            query=q,
            category_code=category or None,
            room=room or None,
            department=department or None,
            status=status or None,
            employee_id=employee_id,
            only_unassigned=bool(unassigned),
            limit=per_page,
            offset=(page - 1) * per_page,
        )
        total = asset_service.count_filtered(
            db,
            query=q,
            category_code=category or None,
            room=room or None,
            department=department or None,
            status=status or None,
            employee_id=employee_id,
            only_unassigned=bool(unassigned),
        )

    return deps.render(
        request,
        "assets.html",
        {
            "assets": assets,
            "total": total,
            "page": page,
            "per_page": per_page,
            "query": q,
            "selected_category": category,
            "selected_status": status,
            "room": room,
            "department": department,
            "employee": employee,
            "unassigned": bool(unassigned),
            "categories": equipment_service.categories(db),
            "statuses": list(EquipmentStatus),
            "employees": asset_service.employees(db),
            "technicians": asset_service.technicians(db),
            "sizes": qr_service.size_choices(),
            "stats": asset_service.stats(db),
            "next_codes": {
                item.code: equipment_service.next_asset_code(db, item.code)
                for item in equipment_service.categories(db)
            },
            "can_manage": asset_service.is_admin(user),
            "review_pending": asset_service.stats(db)["pending_asset_events"],
        },
        user=user,
    )


@router.get("/assets/events", response_class=HTMLResponse)
def asset_events_page(request: Request, pending: str = "yes", db: Session = Depends(get_db)):
    user = deps.require_user(request, db)
    pending_only = pending == "yes"
    stats = asset_service.stats(db)
    movements = asset_service.movement_history(db, pending_only=pending_only, limit=200)
    return deps.render(
        request,
        "asset_events.html",
        {
            "movements": movements,
            "pending_only": pending_only,
            "can_review": asset_service.can_operate(user),
            "review_pending": stats["pending_asset_events"],
            "movements_total": stats["movements_total"],
        },
        user=user,
    )


@router.get("/assets/{asset_id}", response_class=HTMLResponse)
def asset_detail_page(request: Request, asset_id: int, db: Session = Depends(get_db)):
    user = deps.require_user(request, db)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        deps.flash(request, "Qurilma topilmadi.", "error")
        return deps.redirect("/assets")

    qr = asset_service.get_qr(db, asset.id) or qr_service.ensure_qr_code(db, asset, user=user)
    db.commit()

    return deps.render(
        request,
        "asset_detail.html",
        {
            "asset": asset,
            "qr": qr,
            "payload": qr_service.qr_payload(_base_url(request), qr.token),
            "assignments": asset_service.assignment_history(db, asset.id),
            "open_assignment": asset_service.open_assignment(db, asset.id),
            "usage": asset_service.usage_history(db, asset.id),
            "repairs": asset_service.repair_history(db, asset.id),
            "movements": asset_service.movement_history(db, asset.id, limit=50),
            "audit": asset_service.audit_history(db, asset.id),
            "timeline": asset_service.timeline(db, asset, limit=60),
            "cameras": asset_service.camera_links(db, asset.id),
            "employees": asset_service.employees(db),
            "technicians": asset_service.technicians(db),
            "sizes": qr_service.size_choices(),
            "statuses": list(EquipmentStatus),
            "can_manage": asset_service.is_admin(user),
            "can_repair": asset_service.can_repair(user),
            "can_review": asset_service.can_operate(user),
            "open_repair": next((item for item in asset_service.repair_history(db, asset.id) if item.is_open), None),
            "review_pending": asset_service.stats(db)["pending_asset_events"],
        },
        user=user,
    )


@router.get("/qr/{token}", response_class=HTMLResponse)
def resolve_qr_token(request: Request, token: str, db: Session = Depends(get_db)):
    """A scanned label lands here and opens that device's page."""
    user = deps.require_user(request, db)
    asset = qr_service.find_by_token(db, token)
    if asset is None:
        deps.flash(request, "QR kod tanilmadi yoki bekor qilingan.", "error")
        return deps.redirect("/assets")
    asset_service.add_usage(
        db,
        asset,
        "QR_SCANNED",
        user=user,
        note="QR kod orqali ochildi",
        camera_code="MANUAL",
    )
    db.commit()
    return deps.redirect(f"/assets/{asset.id}")


# --------------------------------------------------------------------------- #
# Images / printing
# --------------------------------------------------------------------------- #
@router.get("/assets/{asset_id}/qr.png")
def asset_qr_png(
    request: Request,
    asset_id: int,
    download: int = 1,
    db: Session = Depends(get_db),
):
    deps.require_user(request, db)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        return JSONResponse({"error": "Qurilma topilmadi"}, status_code=404)
    qr = asset_service.get_qr(db, asset.id) or qr_service.ensure_qr_code(db, asset, user=None)
    db.commit()

    png = qr_service.render_qr_png(qr_service.qr_payload(_base_url(request), qr.token), target_px=800)
    filename = f"QR-{asset.asset_code}.png"
    headers = dict(_IMAGE_HEADERS)
    headers["Content-Disposition"] = (
        f'attachment; filename="{filename}"' if download else f'inline; filename="{filename}"'
    )
    return Response(content=png, media_type="image/png", headers=headers)


@router.get("/assets/{asset_id}/label.png")
def asset_label_png(request: Request, asset_id: int, db: Session = Depends(get_db)):
    deps.require_user(request, db)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        return JSONResponse({"error": "Qurilma topilmadi"}, status_code=404)
    qr = asset_service.get_qr(db, asset.id) or qr_service.ensure_qr_code(db, asset, user=None)
    db.commit()

    try:
        size = _size_from_query(request)
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)
    png = qr_service.render_label_png(
        asset,
        qr.token,
        base_url=_base_url(request),
        size=size,
        options=_filtered_options_from_request(request),
    )
    headers = dict(_IMAGE_HEADERS)
    headers["Content-Type"] = "image/png"
    return Response(content=png, media_type="image/png", headers=headers)


@router.get("/assets/{asset_id}/label.css")
def asset_label_css(request: Request, asset_id: int, db: Session = Depends(get_db)):
    """Size-dependent print CSS for the single label page."""
    deps.require_user(request, db)
    if _asset_or_none(db, asset_id) is None:
        return Response(status_code=404)
    try:
        width_mm, height_mm = _size_from_query(request)
    except ValueError:
        width_mm, height_mm = qr_service.SIZE_PRESETS[qr_service.DEFAULT_SIZE]
    return Response(
        content=single_label_css(width_mm, height_mm),
        media_type="text/css",
        headers=_IMAGE_HEADERS,
    )


@router.get("/assets-print.css")
def assets_print_css(request: Request, db: Session = Depends(get_db)):
    """Size/grid dependent print CSS for the A4 sheet."""
    deps.require_user(request, db)
    try:
        size = _size_from_query(request)
        margin = max(0.0, min(25.0, float(request.query_params.get("margin") or 10)))
        gap = max(0.0, min(20.0, float(request.query_params.get("gap") or 3)))
    except ValueError:
        return Response(status_code=400)
    cols, _rows = qr_service.a4_label_capacity(size, margin_mm=margin, gap_mm=gap)
    return Response(
        content=a4_sheet_css(size[0], size[1], cols, margin, gap),
        media_type="text/css",
        headers=_IMAGE_HEADERS,
    )


@router.get("/assets/{asset_id}/print", response_class=HTMLResponse)
def asset_print_page(request: Request, asset_id: int, db: Session = Depends(get_db)):
    """Single-label print preview (print at 100 % scale)."""
    user = deps.require_user(request, db)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        deps.flash(request, "Qurilma topilmadi.", "error")
        return deps.redirect("/assets")
    qr = asset_service.get_qr(db, asset.id) or qr_service.ensure_qr_code(db, asset, user=user)
    db.commit()

    try:
        size = _size_from_query(request)
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect(f"/assets/{asset.id}")

    return deps.render(
        request,
        "asset_label_print.html",
        {
            "asset": asset,
            "qr": qr,
            "size": size,
            "size_key": qr_service.size_key(*size),
            "query_string": str(request.url.query),
            "sheet_css_url": f"/assets/{asset.id}/label.css?{request.url.query}",
            "sizes": qr_service.size_choices(),
            "options": _filtered_options_from_request(request),
            "payload": qr_service.qr_payload(_base_url(request), qr.token),
            "review_pending": 0,
        },
        user=user,
    )


@router.get("/assets-print", response_class=HTMLResponse)
def assets_print_page(
    request: Request,
    asset_ids: list[int] = Query(default=[]),
    db: Session = Depends(get_db),
):
    """A4 sheet with as many labels as fit (``@page`` + CSS grid)."""
    user = deps.require_user(request, db)
    assets = [item for item in (_asset_or_none(db, asset_id) for asset_id in asset_ids) if item is not None]
    if not assets:
        deps.flash(request, "Chop etish uchun kamida bitta qurilma tanlang.", "error")
        return deps.redirect("/assets")

    try:
        size = _size_from_query(request)
        margin = float(request.query_params.get("margin") or 10)
        gap = float(request.query_params.get("gap") or 3)
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/assets")
    margin = max(0.0, min(25.0, margin))
    gap = max(0.0, min(20.0, gap))
    cols, rows = qr_service.a4_label_capacity(size, margin_mm=margin, gap_mm=gap)

    for asset in assets:
        qr_service.ensure_qr_code(db, asset, user=user)
    db.commit()

    return deps.render(
        request,
        "asset_labels_print.html",
        {
            "assets": assets,
            "size": size,
            "size_key": qr_service.size_key(*size),
            "query_string": str(request.url.query),
            "cols": max(1, cols),
            "rows": rows,
            "capacity": max(1, cols) * max(1, rows),
            "margin": margin,
            "gap": gap,
            "sheet_css_url": f"/assets-print.css?{request.url.query}",
            "sizes": qr_service.size_choices(),
            "options": _filtered_options_from_request(request),
            "review_pending": 0,
        },
        user=user,
    )


@router.post("/api/assets/{asset_id}/qr/mark-printed")
def mark_printed(request: Request, asset_id: int, db: Session = Depends(get_db)):
    """Called by the print page right before ``window.print()`` (audit trail)."""
    user = deps.require_user(request, db)
    asset = _asset_or_none(db, asset_id)
    qr = asset_service.get_qr(db, asset.id) if asset is not None else None
    if asset is None or qr is None:
        return JSONResponse({"ok": False}, status_code=404)
    label_size = request.query_params.get("size")
    try:
        label_size = qr_service.size_key(*qr_service.parse_label_size(label_size))
    except ValueError:
        label_size = None
    qr_service.record_print(db, qr, label_size=label_size)
    asset_service.audit_asset(
        db,
        asset,
        "qr.print",
        user=user,
        details={"size": label_size, "reprint": qr.reprint_count},
    )
    db.commit()
    return JSONResponse({"ok": True, "reprint_count": qr.reprint_count})


# --------------------------------------------------------------------------- #
# Mutations
# --------------------------------------------------------------------------- #
@router.post("/api/assets")
def create_asset(
    request: Request,
    category_code: str = Form(...),
    asset_code: Optional[str] = Form(None),
    name: str = Form(""),
    brand: str = Form(""),
    model: str = Form(""),
    serial_number: Optional[str] = Form(None),
    room: str = Form(""),
    location: str = Form(""),
    department: str = Form(""),
    responsible_employee_id: Optional[str] = Form(None),
    responsible_position: str = Form(""),
    technician_employee_id: Optional[str] = Form(None),
    technician_name: str = Form(""),
    purchase_date: Optional[str] = Form(None),
    status: str = Form(EquipmentStatus.IN_STOCK.value),
    notes: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    try:
        purchase = None
        if (purchase_date or "").strip():
            from datetime import date

            purchase = date.fromisoformat(purchase_date.strip())
        asset = asset_service.create_asset(
            db,
            category_code=category_code,
            asset_code=asset_code or None,
            name=name,
            brand=brand,
            model=model,
            serial_number=serial_number or None,
            room=room,
            location=location or room,
            department=department,
            responsible_employee_id=deps.form_int(responsible_employee_id, "Xodim"),
            responsible_position=responsible_position,
            technician_employee_id=deps.form_int(technician_employee_id, "Texnik"),
            technician_name=technician_name,
            purchase_date=purchase,
            status=status,
            notes=notes,
            user=admin,
        )
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect("/assets")
    db.commit()
    deps.flash(request, f"Qurilma qo'shildi va QR kod yaratildi: {asset.asset_code}")
    return deps.redirect(f"/assets/{asset.id}")


@router.post("/api/assets/{asset_id}/update")
def update_asset(
    request: Request,
    asset_id: int,
    name: str = Form(""),
    brand: str = Form(""),
    model: str = Form(""),
    serial_number: Optional[str] = Form(None),
    room: str = Form(""),
    location: str = Form(""),
    department: str = Form(""),
    responsible_position: str = Form(""),
    purchase_date: Optional[str] = Form(None),
    status: str = Form(""),
    notes: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        deps.flash(request, "Qurilma topilmadi.", "error")
        return deps.redirect("/assets")
    try:
        purchase = None
        if (purchase_date or "").strip():
            from datetime import date

            purchase = date.fromisoformat(purchase_date.strip())
        asset_service.update_asset(
            db,
            asset,
            name=name,
            brand=brand,
            model=model,
            serial_number=serial_number,
            room=room,
            location=location,
            department=department,
            responsible_position=responsible_position,
            purchase_date=purchase,
            status=status or None,
            notes=notes,
            user=admin,
        )
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect(f"/assets/{asset_id}")
    db.commit()
    deps.flash(request, "Qurilma ma'lumotlari yangilandi.")
    return deps.redirect(f"/assets/{asset_id}")


@router.post("/api/assets/{asset_id}/assign")
def assign_asset(
    request: Request,
    asset_id: int,
    employee_id: Optional[str] = Form(None),
    position: str = Form(""),
    room: str = Form(""),
    department: str = Form(""),
    notes: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        deps.flash(request, "Qurilma topilmadi.", "error")
        return deps.redirect("/assets")
    try:
        asset_service.assign_asset(
            db,
            asset,
            employee_id=deps.form_int(employee_id, "Xodim"),
            position=position,
            room=room or None,
            department=department or None,
            notes=notes,
            user=admin,
        )
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect(f"/assets/{asset_id}")
    db.commit()
    deps.flash(request, f"Qurilma biriktirildi: {asset.responsible_name}")
    return deps.redirect(f"/assets/{asset_id}")


@router.post("/api/assets/{asset_id}/release")
def release_asset(
    request: Request,
    asset_id: int,
    reason: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        deps.flash(request, "Qurilma topilmadi.", "error")
        return deps.redirect("/assets")
    try:
        asset_service.release_asset(db, asset, reason=reason, user=admin)
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect(f"/assets/{asset_id}")
    db.commit()
    deps.flash(request, "Biriktirish yopildi, tarix saqlandi.")
    return deps.redirect(f"/assets/{asset_id}")


@router.post("/api/assets/{asset_id}/technician")
def assign_technician(
    request: Request,
    asset_id: int,
    technician_employee_id: Optional[str] = Form(None),
    technician_name: str = Form(""),
    notes: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        deps.flash(request, "Qurilma topilmadi.", "error")
        return deps.redirect("/assets")
    try:
        asset_service.assign_technician(
            db,
            asset,
            technician_employee_id=deps.form_int(technician_employee_id, "Texnik"),
            technician_name=technician_name,
            notes=notes,
            user=admin,
        )
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect(f"/assets/{asset_id}")
    db.commit()
    deps.flash(request, f"Texnik biriktirildi: {asset.technician_name}")
    return deps.redirect(f"/assets/{asset_id}")


@router.post("/api/assets/{asset_id}/repair/start")
def start_repair(
    request: Request,
    asset_id: int,
    reason: str = Form(""),
    technician_employee_id: Optional[str] = Form(None),
    technician_name: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = deps.require_roles(request, db, Role.ADMIN, Role.TECHNICIAN)
    deps.verify_csrf(request, csrf_token)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        deps.flash(request, "Qurilma topilmadi.", "error")
        return deps.redirect("/assets")
    try:
        asset_service.start_repair(
            db,
            asset,
            reason=reason,
            technician_employee_id=deps.form_int(technician_employee_id, "Texnik"),
            technician_name=technician_name,
            user=user,
        )
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect(f"/assets/{asset_id}")
    db.commit()
    deps.flash(request, "Qurilma ta'mirga qabul qilindi.")
    return deps.redirect(f"/assets/{asset_id}")


@router.post("/api/assets/{asset_id}/repair/{repair_id}/finish")
def finish_repair(
    request: Request,
    asset_id: int,
    repair_id: int,
    result: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = deps.require_roles(request, db, Role.ADMIN, Role.TECHNICIAN)
    deps.verify_csrf(request, csrf_token)
    record = db.get(AssetRepairRecord, repair_id)
    if record is None or record.asset_id != asset_id:
        deps.flash(request, "Ta'mir yozuvi topilmadi.", "error")
        return deps.redirect(f"/assets/{asset_id}")
    try:
        asset_service.finish_repair(db, record, result=result, user=user)
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect(f"/assets/{asset_id}")
    db.commit()
    deps.flash(request, "Ta'mir yakunlandi.")
    return deps.redirect(f"/assets/{asset_id}")


@router.post("/api/assets/{asset_id}/repair/{repair_id}/return")
def return_repair(
    request: Request,
    asset_id: int,
    repair_id: int,
    note: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    user = deps.require_roles(request, db, Role.ADMIN, Role.TECHNICIAN)
    deps.verify_csrf(request, csrf_token)
    record = db.get(AssetRepairRecord, repair_id)
    if record is None or record.asset_id != asset_id:
        deps.flash(request, "Ta'mir yozuvi topilmadi.", "error")
        return deps.redirect(f"/assets/{asset_id}")
    try:
        asset_service.return_repair(db, record, note=note, user=user)
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect(f"/assets/{asset_id}")
    db.commit()
    deps.flash(request, "Qurilma ta'mirdan qaytarildi.")
    return deps.redirect(f"/assets/{asset_id}")


@router.post("/api/assets/{asset_id}/qr/reissue")
def reissue_qr(
    request: Request,
    asset_id: int,
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        deps.flash(request, "Qurilma topilmadi.", "error")
        return deps.redirect("/assets")
    qr = qr_service.reissue_qr_code(db, asset, user=admin)
    asset_service.audit_asset(db, asset, "qr.reissue", user=admin, details={"token_tail": qr.token[-4:]})
    db.commit()
    deps.flash(request, "Yangi QR kod yaratildi. Eski yorliq endi ishlamaydi.", "warn")
    return deps.redirect(f"/assets/{asset_id}")


@router.post("/api/assets/{asset_id}/camera")
def link_camera(
    request: Request,
    asset_id: int,
    camera_code: str = Form(""),
    zone: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    asset = _asset_or_none(db, asset_id)
    if asset is None:
        deps.flash(request, "Qurilma topilmadi.", "error")
        return deps.redirect("/assets")
    try:
        asset_service.link_camera(db, asset, camera_code=camera_code, zone=zone, user=admin)
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect(f"/assets/{asset_id}")
    db.commit()
    deps.flash(request, "Kamera qurilmaga bog'landi.")
    return deps.redirect(f"/assets/{asset_id}")


@router.post("/api/asset-movements/{movement_id}/review")
def review_movement(
    request: Request,
    movement_id: int,
    decision: str = Form("confirm"),
    note: str = Form(""),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    operator = deps.require_operator(request, db)
    deps.verify_csrf(request, csrf_token)
    movement = db.get(AssetMovementEvent, movement_id)
    if movement is None:
        deps.flash(request, "Kuzatuv yozuvi topilmadi.", "error")
        return deps.redirect("/assets/events")
    try:
        asset_service.confirm_movement(db, movement, confirmed=decision == "confirm", note=note, user=operator)
    except ValueError as exc:
        db.rollback()
        deps.flash(request, str(exc), "error")
        return deps.redirect("/assets/events")
    db.commit()
    deps.flash(request, "Kuzatuv yozuvi ko'rib chiqildi.")
    return deps.redirect("/assets/events")
