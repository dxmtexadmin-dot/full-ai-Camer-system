"""Equipment registry, categories and manual inventory operations."""

from __future__ import annotations

import logging
import re
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import EquipmentAsset, EquipmentCategory, Event, InventoryTransaction, User
from app.models.enums import EquipmentStatus, EventType, TransactionType, VerificationStatus
from app.services import audit_service, event_service, inventory_service
from app.utils.time_utils import utc_now

logger = logging.getLogger(__name__)

CODE_PATTERN = re.compile(r"^[a-z][a-z0-9_]{1,47}$")

CODE_PREFIX = {
    "desktop_pc": "PC",
    "monitor": "MON",
    "all_in_one_pc": "AIO",
    "laptop": "NB",
    "keyboard": "KBD",
    "mouse": "MSE",
    "computer_case": "CASE",
    "printer": "PRN",
    "scanner": "SCN",
    "ups": "UPS",
    "speaker": "SPK",
    "network_device": "NET",
    "other_equipment": "OTH",
    "tv": "TV",
    "projector": "PRJ",
    "ip_phone": "IPP",
    "landline_phone": "TEL",
    "smartphone": "SPH",
    "tablet": "TAB",
    "camera": "CAM",
    "mini_camera": "MCAM",
    "lan_tester": "LT",
    "lan_cable": "CBL",
    "external_hdd": "HDD",
    "flash_drive": "USB",
    "server": "SRV",
}


def categories(session: Session, *, include_inactive: bool = False) -> list[EquipmentCategory]:
    stmt = select(EquipmentCategory).order_by(EquipmentCategory.sort_order)
    if not include_inactive:
        stmt = stmt.where(EquipmentCategory.is_active.is_(True))
    return list(session.scalars(stmt).all())


def category_by_code(session: Session, code: str) -> Optional[EquipmentCategory]:
    return session.scalar(select(EquipmentCategory).where(EquipmentCategory.code == code))


def next_asset_code(session: Session, category_code: str) -> str:
    prefix = CODE_PREFIX.get(category_code, (category_code[:3] or "AST").upper())
    existing = session.scalars(
        select(EquipmentAsset.asset_code).where(EquipmentAsset.asset_code.like(f"{prefix}-%"))
    ).all()
    highest = 0
    for code in existing:
        tail = str(code).split("-")[-1]
        if tail.isdigit():
            highest = max(highest, int(tail))
    return f"{prefix}-{highest + 1:03d}"


def create_asset(
    session: Session,
    *,
    category_code: str,
    asset_code: Optional[str] = None,
    brand: str = "",
    model: str = "",
    serial_number: Optional[str] = None,
    asset_tag: Optional[str] = None,
    location: str = "",
    status: str = EquipmentStatus.IN_STOCK.value,
    notes: Optional[str] = None,
    user=None,
) -> EquipmentAsset:
    category = category_by_code(session, category_code)
    if category is None:
        raise ValueError("Jihoz turi topilmadi.")
    if category.is_person:
        raise ValueError("'Shaxs' toifasiga jihoz qo‘shib bo‘lmaydi.")

    code = (asset_code or next_asset_code(session, category_code)).strip().upper()
    if session.scalar(select(EquipmentAsset).where(EquipmentAsset.asset_code == code)):
        raise ValueError(f"'{code}' inventar kodi allaqachon mavjud.")
    if serial_number:
        duplicate = session.scalar(
            select(EquipmentAsset).where(EquipmentAsset.serial_number == serial_number.strip())
        )
        if duplicate is not None:
            raise ValueError(f"'{serial_number}' seriya raqami allaqachon ro‘yxatda ({duplicate.asset_code}).")

    asset = EquipmentAsset(
        asset_code=code,
        category_id=category.id,
        brand=(brand or "").strip()[:80],
        model=(model or "").strip()[:120],
        serial_number=(serial_number or "").strip() or None,
        asset_tag=(asset_tag or "").strip() or None,
        location=(location or "").strip()[:120],
        status=status,
        notes=(notes or "").strip() or None,
    )
    session.add(asset)
    session.flush()
    audit_service.record(
        session,
        action="asset.create",
        entity="asset",
        entity_id=asset.asset_code,
        details={"category": category_code, "serial": asset.serial_number},
        user=user,
        username="admin",
    )
    return asset


def create_category(
    session: Session,
    *,
    code: str,
    label_uz: str,
    yolo_labels: list[str],
    min_confidence: Optional[float] = None,
    is_active: bool = True,
    sort_order: Optional[int] = None,
    notes: Optional[str] = None,
    user=None,
) -> EquipmentCategory:
    """Register a new equipment type; existing rows are left untouched."""
    normalized = (code or "").strip().lower()
    if not CODE_PATTERN.match(normalized):
        raise ValueError(
            "Kod kichik lotin harflari, raqam va pastki chiziqdan iborat bo‘lishi kerak "
            "(masalan: drill, hammer_drill)."
        )
    if category_by_code(session, normalized) is not None:
        raise ValueError(f"'{normalized}' kodli toifa allaqachon mavjud.")

    label = (label_uz or "").strip()
    if not label:
        raise ValueError("Toifa nomi bo‘sh bo‘lmasligi kerak.")
    labels = [str(item).strip() for item in yolo_labels if str(item).strip()]
    if not labels:
        raise ValueError("Kamida bitta YOLO sinf nomi kerak.")
    if min_confidence is not None and not 0.0 < float(min_confidence) <= 1.0:
        raise ValueError("Minimal ishonch 0 va 1 oralig‘ida bo‘lishi kerak.")

    if sort_order is None:
        # Yangi toifalar ro‘yxat oxirida turadi, mavjudlarining tartibi o‘zgarmaydi.
        highest = session.scalar(select(func.max(EquipmentCategory.sort_order))) or 0
        sort_order = int(highest) + 10

    category = EquipmentCategory(
        code=normalized,
        label_uz=label[:120],
        yolo_labels=labels,
        is_person=False,
        is_active=is_active,
        min_confidence=float(min_confidence) if min_confidence is not None else None,
        sort_order=int(sort_order),
        notes=(notes or "").strip() or None,
    )
    session.add(category)
    session.flush()
    audit_service.record(
        session,
        action="category.create",
        entity="equipment_category",
        entity_id=category.code,
        details={"label_uz": category.label_uz, "yolo_labels": labels},
        user=user,
        username="admin",
    )
    return category


def update_asset(session: Session, asset: EquipmentAsset, *, user=None, **fields) -> EquipmentAsset:
    for field in ("brand", "model", "serial_number", "asset_tag", "location", "notes"):
        if fields.get(field) is not None:
            setattr(asset, field, str(fields[field]).strip() or (None if field in {"serial_number", "asset_tag", "notes"} else ""))
    if fields.get("category_code"):
        category = category_by_code(session, fields["category_code"])
        if category is None or category.is_person:
            raise ValueError("Jihoz turi noto‘g‘ri.")
        asset.category_id = category.id
    if fields.get("status"):
        try:
            asset.status = EquipmentStatus(fields["status"]).value
        except ValueError as exc:
            raise ValueError("Jihoz holati noto‘g‘ri.") from exc
    asset.updated_at = utc_now()
    session.flush()
    audit_service.record(
        session,
        action="asset.update",
        entity="asset",
        entity_id=asset.asset_code,
        details={key: str(value) for key, value in fields.items() if value is not None},
        user=user,
        username="admin",
    )
    return asset


def search_assets(
    session: Session,
    query: str = "",
    *,
    category_code: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 300,
) -> list[EquipmentAsset]:
    stmt = select(EquipmentAsset).order_by(EquipmentAsset.asset_code)
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                EquipmentAsset.asset_code.ilike(like),
                EquipmentAsset.brand.ilike(like),
                EquipmentAsset.model.ilike(like),
                EquipmentAsset.serial_number.ilike(like),
                EquipmentAsset.location.ilike(like),
            )
        )
    if category_code:
        category = category_by_code(session, category_code)
        if category is not None:
            stmt = stmt.where(EquipmentAsset.category_id == category.id)
    if status:
        stmt = stmt.where(EquipmentAsset.status == status)
    return list(session.scalars(stmt.limit(limit)).all())


def count_assets(session: Session, *, status: Optional[str] = None) -> int:
    stmt = select(func.count(EquipmentAsset.id))
    if status:
        stmt = stmt.where(EquipmentAsset.status == status)
    return int(session.scalar(stmt) or 0)


# ---------------------------------------------------------------------------
# Manual inventory operations
# ---------------------------------------------------------------------------
def receive(
    session: Session,
    *,
    category_code: str,
    quantity: int = 1,
    asset_id: Optional[int] = None,
    location: str = "",
    reason: Optional[str] = None,
    user: Optional[User] = None,
) -> InventoryTransaction:
    return inventory_service.record(
        session,
        category_code=category_code,
        transaction_type=TransactionType.RECEIVED,
        quantity=quantity,
        asset_id=asset_id,
        location_to=location,
        is_confirmed=True,
        source="manual",
        reason=reason or "Qo‘lda kirim qilindi",
        user=user,
    )


def remove(
    session: Session,
    *,
    category_code: str,
    quantity: int = 1,
    asset_id: Optional[int] = None,
    location: str = "",
    reason: Optional[str] = None,
    user: Optional[User] = None,
) -> InventoryTransaction:
    return inventory_service.record(
        session,
        category_code=category_code,
        transaction_type=TransactionType.REMOVED,
        quantity=quantity,
        asset_id=asset_id,
        location_from=location,
        is_confirmed=True,
        source="manual",
        reason=reason or "Qo‘lda chiqim qilindi",
        user=user,
    )


def return_item(
    session: Session,
    *,
    category_code: str,
    quantity: int = 1,
    asset_id: Optional[int] = None,
    location: str = "",
    reason: Optional[str] = None,
    user: Optional[User] = None,
) -> InventoryTransaction:
    return inventory_service.record(
        session,
        category_code=category_code,
        transaction_type=TransactionType.RETURNED,
        quantity=quantity,
        asset_id=asset_id,
        location_to=location,
        is_confirmed=True,
        source="manual",
        reason=reason or "Jihoz qaytarildi",
        user=user,
    )


def transfer(
    session: Session,
    *,
    category_code: str,
    location_from: str,
    location_to: str,
    asset_id: Optional[int] = None,
    user: Optional[User] = None,
) -> InventoryTransaction:
    if not location_to.strip():
        raise ValueError("Qayerga ko‘chirilishini kiriting.")
    return inventory_service.record(
        session,
        category_code=category_code,
        transaction_type=TransactionType.TRANSFER,
        quantity=0,
        asset_id=asset_id,
        location_from=location_from,
        location_to=location_to,
        is_confirmed=True,
        source="manual",
        reason="Ichki ko‘chirish",
        user=user,
    )


def correct(
    session: Session,
    *,
    category_code: str,
    delta: int,
    reason: str,
    user: Optional[User] = None,
) -> InventoryTransaction:
    if not reason.strip():
        raise ValueError("Tuzatish sababi kiritilishi shart.")
    return inventory_service.record(
        session,
        category_code=category_code,
        transaction_type=TransactionType.CORRECTION,
        quantity=int(delta),
        is_confirmed=True,
        source="manual",
        reason=reason.strip(),
        user=user,
    )


def set_initial_stock(
    session: Session,
    *,
    category_code: str,
    quantity: int,
    reason: str = "Boshlang‘ich inventar",
    user: Optional[User] = None,
) -> InventoryTransaction:
    return inventory_service.record(
        session,
        category_code=category_code,
        transaction_type=TransactionType.INITIAL,
        quantity=abs(int(quantity)),
        is_confirmed=True,
        source="manual",
        reason=reason,
        user=user,
    )


def register_discrepancy_event(
    session: Session,
    *,
    category_code: str,
    note: str,
    camera_code: str = "SYSTEM",
    user: Optional[User] = None,
) -> Event:
    event, _created = event_service.create_event(
        session,
        event_type=EventType.INVENTORY_DISCREPANCY,
        camera_code=camera_code,
        equipment_category=category_code,
        verification_status=VerificationStatus.PENDING,
        notes=note,
        create_inventory_candidate=False,
    )
    audit_service.record(
        session,
        action="inventory.discrepancy",
        entity="equipment_category",
        entity_id=category_code,
        details={"event": event.public_id},
        reason=note,
        user=user,
        username="operator",
    )
    return event
