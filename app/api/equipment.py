"""Equipment assets, detection categories and manual inventory operations."""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import deps
from app.database import get_db
from app.models import EquipmentAsset, EquipmentCategory
from app.models.enums import EquipmentStatus
from app.services import equipment_service, event_service, inventory_service
from app.services.monitoring_service import get_monitor
from app.utils.time_utils import local_today

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/equipment", response_class=HTMLResponse)
def equipment_page(
    request: Request,
    q: str = "",
    category: str = "",
    status: str = "",
    db: Session = Depends(get_db),
):
    user = deps.require_admin(request, db)
    assets = equipment_service.search_assets(db, q, category_code=category or None, status=status or None)
    categories = equipment_service.categories(db)
    counts = {item.code: 0 for item in categories}
    for asset in db.scalars(select(EquipmentAsset)).all():
        code = asset.category.code if asset.category else None
        if code in counts:
            counts[code] += 1
    return deps.render(
        request,
        "equipment.html",
        {
            "assets": assets,
            "categories": categories,
            "query": q,
            "selected_category": category,
            "selected_status": status,
            "counts": counts,
            "statuses": list(EquipmentStatus),
            "next_codes": {item.code: equipment_service.next_asset_code(db, item.code) for item in categories},
        },
        user=user,
    )


@router.post("/api/equipment")
def create_asset(
    request: Request,
    category_code: str = Form(...),
    asset_code: Optional[str] = Form(None),
    brand: str = Form(""),
    model: str = Form(""),
    serial_number: Optional[str] = Form(None),
    asset_tag: Optional[str] = Form(None),
    location: str = Form(""),
    status: str = Form(EquipmentStatus.IN_STOCK.value),
    notes: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    try:
        asset = equipment_service.create_asset(
            db,
            category_code=category_code,
            asset_code=asset_code or None,
            brand=brand,
            model=model,
            serial_number=serial_number or None,
            asset_tag=asset_tag or None,
            location=location,
            status=status,
            notes=notes,
            user=admin,
        )
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/equipment")
    db.commit()
    deps.flash(request, f"Jihoz ro‘yxatga olindi: {asset.asset_code}")
    return deps.redirect("/equipment")


@router.post("/api/equipment/{asset_id}/update")
def update_asset(
    request: Request,
    asset_id: int,
    brand: str = Form(""),
    model: str = Form(""),
    serial_number: Optional[str] = Form(None),
    asset_tag: Optional[str] = Form(None),
    location: str = Form(""),
    status: str = Form(EquipmentStatus.IN_STOCK.value),
    category_code: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    asset = db.get(EquipmentAsset, asset_id)
    if asset is None:
        deps.flash(request, "Jihoz topilmadi.", "error")
        return deps.redirect("/equipment")
    try:
        equipment_service.update_asset(
            db,
            asset,
            user=admin,
            brand=brand,
            model=model,
            serial_number=serial_number,
            asset_tag=asset_tag,
            location=location,
            status=status,
            category_code=category_code,
            notes=notes,
        )
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/equipment")
    db.commit()
    deps.flash(request, f"Jihoz yangilandi: {asset.asset_code}")
    return deps.redirect("/equipment")


@router.post("/api/categories")
def create_category(
    request: Request,
    code: str = Form(...),
    label_uz: str = Form(...),
    yolo_labels: str = Form(""),
    min_confidence: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    """Add a new equipment type without touching the existing ones."""
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    try:
        confidence_value = deps.form_float(min_confidence, "Minimal ishonch")
        category = equipment_service.create_category(
            db,
            code=code,
            label_uz=label_uz,
            yolo_labels=[item.strip() for item in yolo_labels.split(",") if item.strip()],
            min_confidence=confidence_value,
            notes=notes,
            user=admin,
        )
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/equipment")
    db.commit()
    get_monitor().refresh_detection_rules()
    deps.flash(
        request,
        f"Yangi toifa qo‘shildi: {category.code} ({category.label_uz}). "
        "Eslatma: model bu sinfni tanimasa, jihoz aniqlanmaydi - YOLO qayta o‘qitilishi kerak.",
        "warn",
    )
    return deps.redirect("/equipment")


@router.post("/api/categories/{category_id}/update")
def update_category(
    request: Request,
    category_id: int,
    label_uz: str = Form(...),
    yolo_labels: str = Form(""),
    min_confidence: Optional[str] = Form(None),
    is_active: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    admin = deps.require_admin(request, db)
    deps.verify_csrf(request, csrf_token)
    category = db.get(EquipmentCategory, category_id)
    if category is None:
        deps.flash(request, "Toifa topilmadi.", "error")
        return deps.redirect("/equipment")

    labels = [item.strip() for item in yolo_labels.split(",") if item.strip()]
    if not labels:
        deps.flash(request, "Kamida bitta YOLO sinf nomi kerak.", "error")
        return deps.redirect("/equipment")
    try:
        min_confidence_value = deps.form_float(min_confidence, "Minimal ishonch")
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/equipment")
    if min_confidence_value is not None and not 0.0 < min_confidence_value <= 1.0:
        deps.flash(request, "Minimal ishonch 0 va 1 oralig‘ida bo‘lishi kerak.", "error")
        return deps.redirect("/equipment")
    category.label_uz = label_uz.strip()[:120]
    category.yolo_labels = labels
    category.min_confidence = min_confidence_value
    category.is_active = bool(is_active)
    db.commit()
    get_monitor().refresh_detection_rules()
    deps.flash(request, f"Toifa yangilandi: {category.code} -> {', '.join(labels)}")
    return deps.redirect("/equipment")


# ---------------------------------------------------------------------------
# Manual inventory operations
# ---------------------------------------------------------------------------
@router.get("/inventory", response_class=HTMLResponse)
def inventory_page(
    request: Request,
    category: str = "",
    confirmed: str = "all",
    db: Session = Depends(get_db),
):
    user = deps.require_user(request, db)
    balances = inventory_service.balances(db)
    filtered: Optional[bool] = None
    if confirmed == "yes":
        filtered = True
    elif confirmed == "no":
        filtered = False
    ledger = inventory_service.list_transactions(
        db, category_code=category or None, confirmed=filtered, limit=200
    )
    return deps.render(
        request,
        "inventory.html",
        {
            "balances": balances,
            "ledger": ledger,
            "categories": equipment_service.categories(db),
            "selected_category": category,
            "confirmed_filter": confirmed,
            "pending_totals": inventory_service.pending_movement_totals(db),
            "review_pending": event_service.count_pending(db),
            "today": local_today(),
        },
        user=user,
    )


@router.post("/api/inventory/operation")
def inventory_operation(
    request: Request,
    operation: str = Form(...),
    category_code: str = Form(...),
    quantity: Optional[str] = Form("1"),
    delta: Optional[str] = Form(None),
    location: str = Form(""),
    reason: Optional[str] = Form(None),
    csrf_token: Optional[str] = Form(None),
    db: Session = Depends(get_db),
):
    operator = deps.require_operator(request, db)
    deps.verify_csrf(request, csrf_token)
    try:
        quantity_value = deps.form_int(quantity, "Miqdor")
        if quantity_value is None:
            quantity_value = 1
        if operation == "receive":
            equipment_service.receive(
                db, category_code=category_code, quantity=quantity_value, location=location, reason=reason, user=operator
            )
        elif operation == "remove":
            equipment_service.remove(
                db, category_code=category_code, quantity=quantity_value, location=location, reason=reason, user=operator
            )
        elif operation == "return":
            equipment_service.return_item(
                db, category_code=category_code, quantity=quantity_value, location=location, reason=reason, user=operator
            )
        elif operation == "transfer":
            equipment_service.transfer(
                db,
                category_code=category_code,
                location_from="",
                location_to=location,
                user=operator,
            )
        elif operation == "initial":
            if not operator.is_admin:
                raise ValueError("Boshlang‘ich qoldiqni faqat administrator kiritadi.")
            equipment_service.set_initial_stock(
                db, category_code=category_code, quantity=quantity_value, reason=reason or "Boshlang‘ich inventar", user=operator
            )
        elif operation == "correct":
            delta_value = deps.form_int(delta, "Tuzatish qiymati")
            if delta_value is None:
                raise ValueError("Tuzatish uchun ± qiymat kiritilishi kerak.")
            equipment_service.correct(db, category_code=category_code, delta=int(delta_value), reason=reason or "Tuzatish", user=operator)
        else:
            raise ValueError("Noma’lum amal.")
    except ValueError as exc:
        deps.flash(request, str(exc), "error")
        return deps.redirect("/inventory")
    db.commit()
    deps.flash(request, "Inventar amali bajarildi va jurnalga yozildi.")
    return deps.redirect("/inventory")
