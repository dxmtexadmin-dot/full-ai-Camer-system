"""Device profile, assignment, usage history, repairs and camera observations.

Rules enforced here (never only in the templates):

* one device cannot have two simultaneous *permanent* owners - assigning a new
  responsible employee closes the previous row with an end timestamp instead of
  editing history;
* only an administrator may change the responsible employee;
* a technician may accept and return a device for repair but cannot move the
  permanent ownership;
* camera observations are *facts to review*: they never rewrite the owner and
  an unreadable QR label becomes a PENDING "unidentified" record;
* history tables are append-only - a correction is a new row that points at the
  row it corrects.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import (
    AssetAssignment,
    AssetAuditLog,
    AssetCameraLink,
    AssetMovementEvent,
    AssetQrCode,
    AssetRepairRecord,
    AssetUsageRecord,
    Camera,
    Employee,
    EquipmentAsset,
    Event,
    TechnicianAssignment,
    User,
)
from app.models.enums import (
    AssetMovementType,
    AssignmentKind,
    CameraStatus,
    EquipmentStatus,
    EventType,
    RepairStatus,
    Role,
    UsageAction,
    VerificationStatus,
)
from app.services import audit_service, equipment_service, event_service, qr_service
from app.utils.time_utils import as_utc, utc_now

logger = logging.getLogger(__name__)

MOVEMENT_EVENT_TYPE = {
    AssetMovementType.TAKEN_OUT.value: EventType.EQUIPMENT_TAKEN_OUT,
    AssetMovementType.RETURNED.value: EventType.EQUIPMENT_RETURNED,
    AssetMovementType.MOVED.value: EventType.EQUIPMENT_PICKED_UP,
    AssetMovementType.QR_SCANNED.value: EventType.EQUIPMENT_PICKED_UP,
    AssetMovementType.UNIDENTIFIED.value: EventType.INVENTORY_DISCREPANCY,
}


# --------------------------------------------------------------------------- #
# Roles (defence in depth: the HTTP layer checks too)
# --------------------------------------------------------------------------- #
def _role(user) -> Optional[str]:
    role = getattr(user, "role", None)
    if role is None:
        return None
    return str(role)


def is_admin(user) -> bool:
    return _role(user) == Role.ADMIN.value


def can_view(user) -> bool:
    return _role(user) in {Role.ADMIN.value, Role.OPERATOR.value, Role.TECHNICIAN.value, Role.VIEWER.value}


def can_operate(user) -> bool:
    """Confirm/review inventory observations."""
    return _role(user) in {Role.ADMIN.value, Role.OPERATOR.value}


def can_repair(user) -> bool:
    return _role(user) in {Role.ADMIN.value, Role.TECHNICIAN.value}


def require_admin(user) -> None:
    if not is_admin(user):
        raise PermissionError("Bu amalni faqat administrator bajarishi mumkin.")


def require_operator(user) -> None:
    if not can_operate(user):
        raise PermissionError("Bu amal uchun operator yoki administrator huquqi kerak.")


def require_repair(user) -> None:
    if not can_repair(user):
        raise PermissionError("Ta'mir amali uchun texnik yoki administrator huquqi kerak.")


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _actor(user) -> tuple[Optional[int], str]:
    if user is None:
        return None, "system"
    return getattr(user, "id", None), getattr(user, "username", "system") or "system"


def _clean(value, limit: int) -> str:
    return str(value or "").strip()[:limit]


def get_asset(session: Session, asset_id: int) -> Optional[EquipmentAsset]:
    return session.get(EquipmentAsset, asset_id)


def get_asset_by_code(session: Session, code: str) -> Optional[EquipmentAsset]:
    clean = (code or "").strip().upper()
    if not clean:
        return None
    return session.scalar(select(EquipmentAsset).where(EquipmentAsset.asset_code == clean))


def get_qr(session: Session, asset_id: int) -> Optional[AssetQrCode]:
    return session.scalar(select(AssetQrCode).where(AssetQrCode.asset_id == asset_id))


def resolve_employee(session: Session, employee_id) -> Optional[Employee]:
    try:
        value = int(employee_id)
    except (TypeError, ValueError):
        return None
    return session.get(Employee, value)


def audit_asset(
    session: Session,
    asset: Optional[EquipmentAsset],
    action: str,
    *,
    user=None,
    details: Optional[dict] = None,
    reason: Optional[str] = None,
    is_correction: bool = False,
    corrects_id: Optional[int] = None,
) -> AssetAuditLog:
    user_id, username = _actor(user)
    entry = AssetAuditLog(
        asset_id=asset.id if asset is not None else None,
        actor_user_id=user_id,
        actor_username=username,
        action=action,
        details=details or {},
        reason=(reason or "").strip() or None,
        is_correction=is_correction,
        corrects_id=corrects_id,
    )
    session.add(entry)
    # Keep the global administrator audit trail in sync for compliance.
    audit_service.record(
        session,
        action=f"asset.{action}",
        entity="asset",
        entity_id=(asset.asset_code if asset is not None else ""),
        details=details or {},
        reason=reason,
        user=user,
        username=username,
    )
    return entry


def add_usage(
    session: Session,
    asset: EquipmentAsset,
    action: UsageAction | str,
    *,
    user=None,
    employee: Optional[Employee] = None,
    employee_name: str = "",
    note: Optional[str] = None,
    camera_code: str = "",
    assignment_id: Optional[int] = None,
    occurred_at: Optional[datetime] = None,
    is_correction: bool = False,
    corrects_id: Optional[int] = None,
) -> AssetUsageRecord:
    """Append one row to the "who used it, when" journal."""
    user_id, username = _actor(user)
    name = _clean(employee_name, 160) or (employee.full_name if employee is not None else "")
    record = AssetUsageRecord(
        asset_id=asset.id,
        employee_id=employee.id if employee is not None else None,
        employee_name=name,
        action=str(action),
        actor_user_id=user_id,
        actor_username=username,
        note=(note or "").strip() or None,
        camera_code=camera_code or "",
        assignment_id=assignment_id,
        occurred_at=occurred_at or utc_now(),
        is_correction=is_correction,
        corrects_id=corrects_id,
    )
    session.add(record)
    session.flush()
    return record


# --------------------------------------------------------------------------- #
# Create / update
# --------------------------------------------------------------------------- #
def create_asset(
    session: Session,
    *,
    category_code: str,
    asset_code: Optional[str] = None,
    name: str = "",
    brand: str = "",
    model: str = "",
    serial_number: Optional[str] = None,
    room: str = "",
    location: str = "",
    department: str = "",
    responsible_employee_id=None,
    responsible_position: str = "",
    technician_employee_id=None,
    technician_name: str = "",
    purchase_date=None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
    user=None,
) -> EquipmentAsset:
    """Register a device, fill the QR profile and issue its label token."""
    require_admin(user)

    asset = equipment_service.create_asset(
        session,
        category_code=category_code,
        asset_code=asset_code,
        brand=brand,
        model=model,
        serial_number=serial_number,
        location=location or room,
        status=status or EquipmentStatus.IN_STOCK.value,
        notes=notes,
        user=user,
    )

    asset.name = _clean(name, 160)
    asset.room = _clean(room, 80)
    asset.department = _clean(department, 120)
    asset.responsible_position = _clean(responsible_position, 120)
    asset.purchase_date = purchase_date

    employee = resolve_employee(session, responsible_employee_id)
    if employee is not None:
        asset.responsible_employee_id = employee.id
        asset.responsible_name = employee.full_name
        asset.department = asset.department or employee.department
        asset.status = EquipmentStatus.IN_USE.value

    technician = resolve_employee(session, technician_employee_id)
    if technician is not None:
        asset.technician_employee_id = technician.id
        asset.technician_name = technician.full_name
    else:
        asset.technician_name = _clean(technician_name, 160)

    session.flush()

    qr_service.ensure_qr_code(session, asset, user=user)
    add_usage(session, asset, UsageAction.CREATED, user=user, employee=employee, note="Qurilma ro'yxatga olindi")
    if employee is not None:
        _open_assignment(
            session,
            asset,
            employee=employee,
            kind=AssignmentKind.RESPONSIBLE,
            room=asset.room,
            department=asset.department,
            position=asset.responsible_position,
            user=user,
            note="Ro'yxatga olishda biriktirildi",
        )
    audit_asset(
        session,
        asset,
        "create",
        user=user,
        details={
            "category": category_code,
            "serial": asset.serial_number,
            "name": asset.name,
            "room": asset.room,
            "responsible": asset.responsible_name,
        },
    )
    session.flush()
    return asset


def update_asset(
    session: Session,
    asset: EquipmentAsset,
    *,
    name: Optional[str] = None,
    brand: Optional[str] = None,
    model: Optional[str] = None,
    serial_number: Optional[str] = None,
    room: Optional[str] = None,
    location: Optional[str] = None,
    department: Optional[str] = None,
    responsible_position: Optional[str] = None,
    purchase_date=None,
    status: Optional[str] = None,
    notes: Optional[str] = None,
    user=None,
) -> EquipmentAsset:
    """Edit descriptive fields (never the owner - that is ``assign_asset``)."""
    require_admin(user)
    changes: dict[str, dict] = {}

    def apply(field: str, value, limit: int = 160) -> None:
        if value is None:
            return
        clean = _clean(value, limit)
        current = getattr(asset, field) or ""
        if clean != current:
            changes[field] = {"from": current, "to": clean}
            setattr(asset, field, clean)

    apply("name", name)
    apply("brand", brand, 80)
    apply("model", model, 120)
    apply("room", room, 80)
    apply("department", department, 120)
    apply("responsible_position", responsible_position, 120)
    apply("serial_number", serial_number, 120)
    apply("location", location, 120)
    apply("notes", notes, 2000)
    if purchase_date is not None:
        if asset.purchase_date != purchase_date:
            changes["purchase_date"] = {"from": str(asset.purchase_date or ""), "to": str(purchase_date)}
            asset.purchase_date = purchase_date
    if status:
        try:
            value = EquipmentStatus(status).value
        except ValueError as exc:
            raise ValueError("Qurilma holati noto'g'ri.") from exc
        if asset.status != value:
            changes["status"] = {"from": asset.status, "to": value}
            asset.status = value

    if changes:
        asset.updated_at = utc_now()
        session.flush()
        add_usage(
            session,
            asset,
            UsageAction.UPDATED,
            user=user,
            note="Ma'lumot o'zgartirildi: " + ", ".join(sorted(changes)),
        )
        audit_asset(session, asset, "update", user=user, details=changes)
    session.flush()
    return asset


# --------------------------------------------------------------------------- #
# Assignment (permanent owner)
# --------------------------------------------------------------------------- #
def open_assignment(
    session: Session,
    asset_id: int,
    kind: AssignmentKind | str = AssignmentKind.RESPONSIBLE,
) -> Optional[AssetAssignment]:
    return session.scalar(
        select(AssetAssignment)
        .where(
            AssetAssignment.asset_id == asset_id,
            AssetAssignment.kind == str(kind),
            AssetAssignment.ended_at.is_(None),
        )
        .order_by(AssetAssignment.assigned_at.desc())
    )


def _close_assignment(
    session: Session,
    assignment: AssetAssignment,
    *,
    user=None,
    reason: str = "",
) -> None:
    assignment.ended_at = utc_now()
    user_id, _username = _actor(user)
    assignment.ended_by_user_id = user_id
    assignment.end_reason = (reason or "").strip() or None
    session.flush()


def _open_assignment(
    session: Session,
    asset: EquipmentAsset,
    *,
    employee: Optional[Employee],
    kind: AssignmentKind | str,
    employee_name: str = "",
    room: str = "",
    department: str = "",
    position: str = "",
    user=None,
    note: str = "",
) -> AssetAssignment:
    user_id, username = _actor(user)
    assignment = AssetAssignment(
        asset_id=asset.id,
        employee_id=employee.id if employee is not None else None,
        employee_name=employee.full_name if employee is not None else _clean(employee_name, 160),
        employee_position=_clean(position, 120),
        kind=str(kind),
        room=_clean(room, 80),
        department=_clean(department, 120),
        assigned_by_user_id=user_id,
        assigned_by_username=username,
        notes=(note or "").strip() or None,
    )
    session.add(assignment)
    session.flush()
    return assignment


def assign_asset(
    session: Session,
    asset: EquipmentAsset,
    *,
    employee_id,
    position: str = "",
    room: Optional[str] = None,
    department: Optional[str] = None,
    kind: AssignmentKind | str = AssignmentKind.RESPONSIBLE,
    notes: str = "",
    user=None,
) -> AssetAssignment:
    """Give the device to an employee; the previous owner keeps their history."""
    require_admin(user)
    employee = resolve_employee(session, employee_id)
    if employee is None:
        raise ValueError("Xodim tanlanmadi yoki topilmadi.")
    if not employee.is_active:
        raise ValueError(f"{employee.full_name} faol emas - biriktirish mumkin emas.")

    previous = open_assignment(session, asset.id, kind)
    if previous is not None:
        if previous.employee_id == employee.id:
            raise ValueError(f"{employee.full_name} allaqachon ushbu qurilmaning mas'uli.")
        _close_assignment(session, previous, user=user, reason=notes or "Boshqa xodimga o'tkazildi")
        add_usage(
            session,
            asset,
            UsageAction.RELEASED,
            user=user,
            employee_name=previous.employee_name,
            note=f"Mas'uliyat yakunlandi ({previous.employee_name})",
            assignment_id=previous.id,
        )

    room_value = _clean(room if room is not None else asset.room, 80)
    department_value = _clean(department if department is not None else (employee.department or asset.department), 120)
    position_value = _clean(position, 120) or asset.responsible_position or employee.department

    assignment = _open_assignment(
        session,
        asset,
        employee=employee,
        kind=kind,
        room=room_value,
        department=department_value,
        position=position_value,
        user=user,
        note=notes,
    )

    if str(kind) == AssignmentKind.RESPONSIBLE.value:
        asset.responsible_employee_id = employee.id
        asset.responsible_name = employee.full_name
        asset.responsible_position = position_value
        asset.room = room_value or asset.room
        asset.department = department_value or asset.department
        asset.location = room_value or asset.location
        if asset.status in {EquipmentStatus.IN_STOCK.value, EquipmentStatus.RETIRED.value}:
            asset.status = EquipmentStatus.IN_USE.value
        asset.updated_at = utc_now()

    action = UsageAction.TRANSFERRED if previous is not None else UsageAction.ASSIGNED
    add_usage(
        session,
        asset,
        action,
        user=user,
        employee=employee,
        note=notes or (f"{previous.employee_name} -> {employee.full_name}" if previous else "Qurilma biriktirildi"),
        assignment_id=assignment.id,
    )
    audit_asset(
        session,
        asset,
        "transfer" if previous is not None else "assign",
        user=user,
        details={
            "employee": employee.full_name,
            "employee_id": employee.id,
            "kind": str(kind),
            "room": room_value,
            "previous": previous.employee_name if previous else "",
        },
        reason=notes,
    )
    session.flush()
    return assignment


def release_asset(session: Session, asset: EquipmentAsset, *, reason: str = "", user=None) -> None:
    """End the current ownership (device goes back to stock)."""
    require_admin(user)
    assignment = open_assignment(session, asset.id, AssignmentKind.RESPONSIBLE)
    if assignment is None:
        raise ValueError("Ushbu qurilma hech kimga biriktirilmagan.")
    _close_assignment(session, assignment, user=user, reason=reason)
    add_usage(
        session,
        asset,
        UsageAction.RELEASED,
        user=user,
        employee_name=assignment.employee_name,
        note=reason or "Qurilma qaytarib olindi",
        assignment_id=assignment.id,
    )
    asset.responsible_employee_id = None
    asset.responsible_name = ""
    asset.responsible_position = ""
    if asset.status == EquipmentStatus.IN_USE.value:
        asset.status = EquipmentStatus.IN_STOCK.value
    asset.updated_at = utc_now()
    audit_asset(session, asset, "release", user=user, details={"employee": assignment.employee_name}, reason=reason)
    session.flush()


def assignment_history(session: Session, asset_id: int) -> list[AssetAssignment]:
    return list(
        session.scalars(
            select(AssetAssignment)
            .where(AssetAssignment.asset_id == asset_id)
            .order_by(AssetAssignment.assigned_at.desc())
        ).all()
    )


# --------------------------------------------------------------------------- #
# Technician
# --------------------------------------------------------------------------- #
def open_technician_assignment(session: Session, asset_id: int) -> Optional[TechnicianAssignment]:
    return session.scalar(
        select(TechnicianAssignment)
        .where(TechnicianAssignment.asset_id == asset_id, TechnicianAssignment.ended_at.is_(None))
        .order_by(TechnicianAssignment.assigned_at.desc())
    )


def assign_technician(
    session: Session,
    asset: EquipmentAsset,
    *,
    technician_employee_id=None,
    technician_name: str = "",
    notes: str = "",
    user=None,
) -> TechnicianAssignment:
    require_admin(user)
    employee = resolve_employee(session, technician_employee_id)
    name = employee.full_name if employee is not None else _clean(technician_name, 160)
    if not name:
        raise ValueError("Texnik tanlanmadi.")

    previous = open_technician_assignment(session, asset.id)
    if previous is not None:
        previous.ended_at = utc_now()
        session.flush()

    user_id, _username = _actor(user)
    record = TechnicianAssignment(
        asset_id=asset.id,
        technician_employee_id=employee.id if employee is not None else None,
        technician_name=name,
        assigned_by_user_id=user_id,
        notes=(notes or "").strip() or None,
    )
    session.add(record)
    asset.technician_employee_id = employee.id if employee is not None else None
    asset.technician_name = name
    asset.updated_at = utc_now()
    session.flush()
    audit_asset(session, asset, "technician.assign", user=user, details={"technician": name}, reason=notes)
    return record


def technician_assets(session: Session, user) -> list[EquipmentAsset]:
    """Devices a technician is responsible for servicing."""
    if is_admin(user):
        return list(
            session.scalars(select(EquipmentAsset).order_by(EquipmentAsset.asset_code).limit(500)).all()
        )
    names = {(_clean(getattr(user, "full_name", ""), 160)).lower(), (_clean(getattr(user, "username", ""), 64)).lower()}
    names.discard("")
    if not names:
        return []
    stmt = select(EquipmentAsset).where(func.lower(EquipmentAsset.technician_name).in_(names))
    return list(session.scalars(stmt.order_by(EquipmentAsset.asset_code)).all())


# --------------------------------------------------------------------------- #
# Repairs
# --------------------------------------------------------------------------- #
def start_repair(
    session: Session,
    asset: EquipmentAsset,
    *,
    reason: str,
    technician_employee_id=None,
    technician_name: str = "",
    user=None,
) -> AssetRepairRecord:
    require_repair(user)
    if not (reason or "").strip():
        raise ValueError("Ta'mir sababi kiritilishi shart.")
    existing = session.scalar(
        select(AssetRepairRecord).where(
            AssetRepairRecord.asset_id == asset.id,
            AssetRepairRecord.status.in_([RepairStatus.OPEN.value, RepairStatus.IN_PROGRESS.value]),
        )
    )
    if existing is not None:
        raise ValueError("Ushbu qurilma uchun ochiq ta'mir yozuvi allaqachon mavjud.")

    employee = resolve_employee(session, technician_employee_id)
    name = employee.full_name if employee is not None else _clean(technician_name, 160)
    if not name:
        name = getattr(user, "full_name", "") or getattr(user, "username", "Texnik")

    user_id, _username = _actor(user)
    record = AssetRepairRecord(
        asset_id=asset.id,
        technician_employee_id=employee.id if employee is not None else None,
        technician_name=name,
        reason=reason.strip(),
        status=RepairStatus.IN_PROGRESS.value,
        started_at=utc_now(),
        received_by_user_id=user_id,
    )
    session.add(record)
    asset.status = EquipmentStatus.MAINTENANCE.value
    asset.technician_name = name or asset.technician_name
    asset.updated_at = utc_now()
    session.flush()

    add_usage(
        session,
        asset,
        UsageAction.REPAIR_RECEIVED,
        user=user,
        employee=employee,
        employee_name=name,
        note=reason.strip(),
    )
    audit_asset(session, asset, "repair.start", user=user, details={"technician": name}, reason=reason)
    return record


def finish_repair(session: Session, record: AssetRepairRecord, *, result: str = "", user=None) -> AssetRepairRecord:
    require_repair(user)
    if record.status in {RepairStatus.DONE.value, RepairStatus.RETURNED.value, RepairStatus.CANCELLED.value}:
        raise ValueError("Bu ta'mir yozuvi allaqachon yakunlangan.")
    record.status = RepairStatus.DONE.value
    record.finished_at = utc_now()
    record.result = (result or "").strip() or None
    session.flush()
    asset = record.asset or get_asset(session, record.asset_id)
    if asset is not None:
        add_usage(
            session,
            asset,
            UsageAction.REPAIR_FINISHED,
            user=user,
            employee_name=record.technician_name,
            note=record.result or "Ta'mir yakunlandi",
        )
        audit_asset(session, asset, "repair.finish", user=user, details={"result": record.result})
    return record


def return_repair(session: Session, record: AssetRepairRecord, *, note: str = "", user=None) -> AssetRepairRecord:
    """Hand the repaired device back to its owner."""
    require_repair(user)
    if record.status == RepairStatus.RETURNED.value:
        raise ValueError("Qurilma allaqachon qaytarilgan.")
    if record.finished_at is None:
        record.finished_at = utc_now()
    record.status = RepairStatus.RETURNED.value
    record.returned_at = utc_now()
    if note:
        record.result = (record.result or "") + ("\n" if record.result else "") + note.strip()
    session.flush()

    asset = record.asset or get_asset(session, record.asset_id)
    if asset is not None:
        asset.status = (
            EquipmentStatus.IN_USE.value if asset.responsible_employee_id else EquipmentStatus.IN_STOCK.value
        )
        asset.updated_at = utc_now()
        add_usage(
            session,
            asset,
            UsageAction.REPAIR_RETURNED,
            user=user,
            employee_name=record.technician_name,
            note=note or "Ta'mirdan qaytarildi",
        )
        audit_asset(session, asset, "repair.return", user=user, details={"technician": record.technician_name})
    return record


def repair_history(session: Session, asset_id: int) -> list[AssetRepairRecord]:
    return list(
        session.scalars(
            select(AssetRepairRecord)
            .where(AssetRepairRecord.asset_id == asset_id)
            .order_by(AssetRepairRecord.started_at.desc())
        ).all()
    )


def open_repairs(session: Session) -> list[AssetRepairRecord]:
    return list(
        session.scalars(
            select(AssetRepairRecord)
            .where(AssetRepairRecord.status.in_([RepairStatus.OPEN.value, RepairStatus.IN_PROGRESS.value]))
            .order_by(AssetRepairRecord.started_at.desc())
        ).all()
    )


# --------------------------------------------------------------------------- #
# Camera observations
# --------------------------------------------------------------------------- #
def _minute_key(moment: datetime) -> str:
    return as_utc(moment).strftime("%Y%m%d%H%M")


def _movement_key(code: str, camera_code: str, movement: str, moment: datetime) -> str:
    """One camera fact per device + camera + minute (frames arrive continuously)."""
    return f"amv:{code or '-'}:{camera_code or '-'}:{movement}:{_minute_key(moment)}"


def record_movement(
    session: Session,
    *,
    camera_code: str,
    movement_type: AssetMovementType | str,
    asset: Optional[EquipmentAsset] = None,
    asset_code: str = "",
    detected_category: Optional[str] = None,
    qr_token: Optional[str] = None,
    confidence: Optional[float] = None,
    verification_status: VerificationStatus | str = VerificationStatus.PENDING,
    evidence_path: Optional[str] = None,
    employee_id: Optional[int] = None,
    notes: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
    event_id: Optional[int] = None,
) -> tuple[AssetMovementEvent, bool]:
    """Store one camera observation. Duplicates inside a minute collapse."""
    moment = occurred_at or utc_now()
    movement = str(movement_type)
    code = asset.asset_code if asset is not None else _clean(asset_code, 48)
    key = _movement_key(code, camera_code, movement, moment)

    existing = session.scalar(select(AssetMovementEvent).where(AssetMovementEvent.dedupe_key == key))
    if existing is not None:
        return existing, False

    record = AssetMovementEvent(
        asset_id=asset.id if asset is not None else None,
        asset_code=code,
        camera_code=camera_code or "",
        movement_type=movement,
        detected_category=detected_category,
        qr_token=qr_token,
        confidence=confidence,
        verification_status=str(verification_status),
        evidence_path=evidence_path,
        event_id=event_id,
        notes=(notes or "").strip() or None,
        occurred_at=moment,
        dedupe_key=key,
    )
    session.add(record)
    try:
        session.flush()
    except Exception:  # unique race - another worker stored the same observation
        session.rollback()
        existing = session.scalar(select(AssetMovementEvent).where(AssetMovementEvent.dedupe_key == key))
        if existing is not None:
            return existing, False
        raise
    return record, True


def observe_movement(
    session: Session,
    *,
    camera_code: str,
    movement_type: AssetMovementType | str,
    asset: Optional[EquipmentAsset] = None,
    detected_category: Optional[str] = None,
    confidence: Optional[float] = None,
    evidence_path: Optional[str] = None,
    employee_id: Optional[int] = None,
    notes: Optional[str] = None,
    occurred_at: Optional[datetime] = None,
) -> Optional[AssetMovementEvent]:
    """Create the review ``Event`` first, then the asset-specific fact.

    ``PENDING`` observations never change the owner; they wait for a human.
    """
    movement = str(movement_type)
    event_type = MOVEMENT_EVENT_TYPE.get(movement, EventType.INVENTORY_DISCREPANCY)
    status = VerificationStatus.PENDING if asset is None else VerificationStatus.AUTO
    if movement == AssetMovementType.QR_SCANNED.value:
        status = VerificationStatus.CONFIRMED
    key = _movement_key(
        asset.asset_code if asset is not None else "", camera_code, movement, occurred_at or utc_now()
    )

    event, _created = event_service.create_event(
        session,
        event_type=event_type,
        camera_code=camera_code or "SYSTEM",
        occurred_at=occurred_at,
        employee_id=employee_id,
        asset_id=asset.id if asset is not None else None,
        equipment_category=detected_category,
        detection_confidence=confidence,
        verification_status=status,
        evidence_path=evidence_path,
        notes=notes,
        extra={"source": "asset_monitor", "movement": movement},
        dedupe_key=key,
        cooldown_seconds=60,
        create_inventory_candidate=False,
    )
    record, created = record_movement(
        session,
        camera_code=camera_code,
        movement_type=movement,
        asset=asset,
        detected_category=detected_category,
        confidence=confidence,
        verification_status=status,
        evidence_path=evidence_path,
        notes=notes,
        occurred_at=occurred_at,
        event_id=event.id,
    )
    if not created:
        logger.debug("Duplicate camera observation ignored: %s %s", camera_code, movement)
    return record


def confirm_movement(
    session: Session,
    movement: AssetMovementEvent,
    *,
    confirmed: bool,
    note: str = "",
    user=None,
) -> AssetMovementEvent:
    """Administrator/operator decision on a camera observation."""
    require_operator(user)
    if str(movement.verification_status) != VerificationStatus.PENDING.value:
        raise ValueError("Bu yozuv allaqachon ko'rib chiqilgan.")
    movement.verification_status = (
        VerificationStatus.CONFIRMED.value if confirmed else VerificationStatus.REJECTED.value
    )
    if note:
        movement.notes = ((movement.notes or "") + " | " + note.strip()).strip(" |")
    session.flush()

    # The linked review event carries the same decision so the existing
    # ``/review`` queue stays consistent; a rejected event never changes stock.
    if movement.event_id:
        event = session.get(Event, movement.event_id)
        if event is not None and event.verification_status == VerificationStatus.PENDING.value:
            event_service.review_event(
                session,
                event,
                decision=movement.verification_status,
                user=user,
                reason=note or "Inventar kuzatuvi qarori",
            )

    asset = get_asset(session, movement.asset_id) if movement.asset_id else None
    if asset is not None:
        audit_asset(
            session,
            asset,
            "movement.confirm" if confirmed else "movement.reject",
            user=user,
            details={"movement": movement.movement_type, "camera": movement.camera_code},
            reason=note,
        )
    return movement


def link_camera(session: Session, asset: EquipmentAsset, *, camera_code: str, zone: str = "", user=None) -> AssetCameraLink:
    require_admin(user)
    code = _clean(camera_code, 32)
    if not code:
        raise ValueError("Kamera tanlanmadi.")
    existing = session.scalar(
        select(AssetCameraLink).where(
            AssetCameraLink.asset_id == asset.id, AssetCameraLink.camera_code == code
        )
    )
    if existing is not None:
        existing.is_active = True
        existing.zone = _clean(zone, 120)
    else:
        existing = AssetCameraLink(asset_id=asset.id, camera_code=code, zone=_clean(zone, 120))
        session.add(existing)
    session.flush()
    audit_asset(session, asset, "camera.link", user=user, details={"camera": code, "zone": zone})
    return existing


def unlink_camera(session: Session, asset: EquipmentAsset, camera_code: str, *, user=None) -> None:
    require_admin(user)
    link = session.scalar(
        select(AssetCameraLink).where(
            AssetCameraLink.asset_id == asset.id, AssetCameraLink.camera_code == camera_code
        )
    )
    if link is None:
        return
    link.is_active = False
    session.flush()
    audit_asset(session, asset, "camera.unlink", user=user, details={"camera": camera_code})


def camera_links(session: Session, asset_id: int) -> list[AssetCameraLink]:
    return list(
        session.scalars(
            select(AssetCameraLink)
            .where(AssetCameraLink.asset_id == asset_id)
            .order_by(AssetCameraLink.camera_code)
        ).all()
    )


def scan_qr(
    session: Session,
    token: str,
    *,
    camera_code: str = "SYSTEM",
    employee_id: Optional[int] = None,
    user=None,
) -> Optional[EquipmentAsset]:
    """Record a successful label read. Unknown tokens return ``None``."""
    asset = qr_service.find_by_token(session, token)
    if asset is None:
        return None
    observe_movement(
        session,
        camera_code=camera_code,
        movement_type=AssetMovementType.QR_SCANNED,
        asset=asset,
        notes=f"QR o'qildi: {asset.asset_code}",
        employee_id=employee_id,
    )
    add_usage(
        session,
        asset,
        UsageAction.QR_SCANNED,
        user=user,
        note=f"QR skanerlandi ({camera_code})",
        camera_code=camera_code,
    )
    return asset


def record_unidentified(
    session: Session,
    *,
    camera_code: str,
    detected_category: Optional[str] = None,
    evidence_path: Optional[str] = None,
    confidence: Optional[float] = None,
    notes: str = "QR o'qilmadi - aniq identifikatsiya qilinmagan",
) -> Optional[AssetMovementEvent]:
    """Fallback when the label cannot be read: save it and ask a human."""
    return observe_movement(
        session,
        camera_code=camera_code,
        movement_type=AssetMovementType.UNIDENTIFIED,
        asset=None,
        detected_category=detected_category,
        evidence_path=evidence_path,
        confidence=confidence,
        notes=notes,
    )


# --------------------------------------------------------------------------- #
# Queries for the UI
# --------------------------------------------------------------------------- #
def _apply_filters(
    stmt,
    session: Session,
    *,
    query: str = "",
    category_code: Optional[str] = None,
    room: Optional[str] = None,
    department: Optional[str] = None,
    status: Optional[str] = None,
    employee_id: Optional[int] = None,
    only_unassigned: bool = False,
):
    if query:
        like = f"%{query.strip()}%"
        stmt = stmt.where(
            or_(
                EquipmentAsset.asset_code.ilike(like),
                EquipmentAsset.name.ilike(like),
                EquipmentAsset.brand.ilike(like),
                EquipmentAsset.model.ilike(like),
                EquipmentAsset.serial_number.ilike(like),
                EquipmentAsset.responsible_name.ilike(like),
                EquipmentAsset.room.ilike(like),
                EquipmentAsset.technician_name.ilike(like),
            )
        )
    if category_code:
        category = equipment_service.category_by_code(session, category_code)
        if category is not None:
            stmt = stmt.where(EquipmentAsset.category_id == category.id)
    if room:
        stmt = stmt.where(EquipmentAsset.room.ilike(f"%{room.strip()}%"))
    if department:
        stmt = stmt.where(EquipmentAsset.department.ilike(f"%{department.strip()}%"))
    if status:
        stmt = stmt.where(EquipmentAsset.status == status)
    if employee_id:
        stmt = stmt.where(EquipmentAsset.responsible_employee_id == int(employee_id))
    if only_unassigned:
        stmt = stmt.where(EquipmentAsset.responsible_employee_id.is_(None))
    return stmt


def filtered_assets(
    session: Session,
    *,
    query: str = "",
    category_code: Optional[str] = None,
    room: Optional[str] = None,
    department: Optional[str] = None,
    status: Optional[str] = None,
    employee_id: Optional[int] = None,
    only_unassigned: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[EquipmentAsset]:
    stmt = _apply_filters(
        select(EquipmentAsset).order_by(EquipmentAsset.asset_code),
        session,
        query=query,
        category_code=category_code,
        room=room,
        department=department,
        status=status,
        employee_id=employee_id,
        only_unassigned=only_unassigned,
    )
    return list(session.scalars(stmt.offset(max(0, offset)).limit(max(1, limit))).all())


def count_filtered(
    session: Session,
    *,
    query: str = "",
    category_code: Optional[str] = None,
    room: Optional[str] = None,
    department: Optional[str] = None,
    status: Optional[str] = None,
    employee_id: Optional[int] = None,
    only_unassigned: bool = False,
) -> int:
    stmt = _apply_filters(
        select(func.count(EquipmentAsset.id)),
        session,
        query=query,
        category_code=category_code,
        room=room,
        department=department,
        status=status,
        employee_id=employee_id,
        only_unassigned=only_unassigned,
    )
    return int(session.scalar(stmt) or 0)


def usage_history(session: Session, asset_id: int, *, limit: int = 200) -> list[AssetUsageRecord]:
    return list(
        session.scalars(
            select(AssetUsageRecord)
            .where(AssetUsageRecord.asset_id == asset_id)
            .order_by(AssetUsageRecord.occurred_at.desc(), AssetUsageRecord.id.desc())
            .limit(limit)
        ).all()
    )


def movement_history(
    session: Session,
    asset_id: Optional[int] = None,
    *,
    pending_only: bool = False,
    limit: int = 200,
) -> list[AssetMovementEvent]:
    stmt = select(AssetMovementEvent).order_by(AssetMovementEvent.occurred_at.desc())
    if asset_id is not None:
        stmt = stmt.where(AssetMovementEvent.asset_id == asset_id)
    if pending_only:
        stmt = stmt.where(AssetMovementEvent.verification_status == VerificationStatus.PENDING.value)
    return list(session.scalars(stmt.limit(limit)).all())


def audit_history(session: Session, asset_id: int, *, limit: int = 200) -> list[AssetAuditLog]:
    return list(
        session.scalars(
            select(AssetAuditLog)
            .where(AssetAuditLog.asset_id == asset_id)
            .order_by(AssetAuditLog.occurred_at.desc(), AssetAuditLog.id.desc())
            .limit(limit)
        ).all()
    )


def timeline(session: Session, asset: EquipmentAsset, *, limit: int = 200) -> list[dict]:
    """One merged, newest-first history of facts about the device."""
    rows: list[dict] = []
    for record in usage_history(session, asset.id, limit=limit):
        rows.append(
            {
                "at": record.occurred_at,
                "kind": "usage",
                "title": record.action_label_uz,
                "actor": record.actor_username,
                "employee": record.employee_name,
                "detail": record.note or "",
                "camera": record.camera_code,
                "status": "",
                "correction": record.is_correction,
            }
        )
    for repair in repair_history(session, asset.id):
        rows.append(
            {
                "at": repair.started_at,
                "kind": "repair",
                "title": f"Ta'mir: {repair.status_label_uz}",
                "actor": repair.technician_name,
                "employee": repair.technician_name,
                "detail": repair.reason or "",
                "camera": "",
                "status": repair.status,
                "correction": False,
            }
        )
    for movement in movement_history(session, asset.id, limit=limit):
        rows.append(
            {
                "at": movement.occurred_at,
                "kind": "camera",
                "title": movement.movement_label_uz,
                "actor": movement.camera_code,
                "employee": "",
                "detail": movement.notes or "",
                "camera": movement.camera_code,
                "status": movement.verification_status,
                "correction": False,
            }
        )
    # SQLite returns naive datetimes; normalise before mixing the three sources.
    rows.sort(key=lambda item: as_utc(item["at"]), reverse=True)
    return rows[:limit]


def stats(session: Session) -> dict:
    """Counters for the dashboard card row."""
    total = int(session.scalar(select(func.count(EquipmentAsset.id))) or 0)
    assigned = int(
        session.scalar(
            select(func.count(EquipmentAsset.id)).where(EquipmentAsset.responsible_employee_id.isnot(None))
        )
        or 0
    )
    with_technician = int(
        session.scalar(
            select(func.count(EquipmentAsset.id)).where(EquipmentAsset.technician_name != "")
        )
        or 0
    )
    in_repair = int(
        session.scalar(
            select(func.count(func.distinct(AssetRepairRecord.asset_id))).where(
                AssetRepairRecord.status.in_([RepairStatus.OPEN.value, RepairStatus.IN_PROGRESS.value])
            )
        )
        or 0
    )
    pending_movements = int(
        session.scalar(
            select(func.count(AssetMovementEvent.id)).where(
                AssetMovementEvent.verification_status == VerificationStatus.PENDING.value
            )
        )
        or 0
    )
    total_movements = int(session.scalar(select(func.count(AssetMovementEvent.id))) or 0)

    latest = (
        select(
            AssetMovementEvent.asset_id.label("asset_id"),
            func.max(AssetMovementEvent.occurred_at).label("last_at"),
        )
        .where(AssetMovementEvent.asset_id.isnot(None))
        .group_by(AssetMovementEvent.asset_id)
        .subquery()
    )
    removed = int(
        session.scalar(
            select(func.count(AssetMovementEvent.id))
            .join(
                latest,
                (AssetMovementEvent.asset_id == latest.c.asset_id)
                & (AssetMovementEvent.occurred_at == latest.c.last_at),
            )
            .where(
                AssetMovementEvent.movement_type == AssetMovementType.TAKEN_OUT.value,
                AssetMovementEvent.verification_status != VerificationStatus.REJECTED.value,
            )
        )
        or 0
    )

    cameras_online = int(
        session.scalar(select(func.count(Camera.id)).where(Camera.status == CameraStatus.ONLINE.value)) or 0
    )
    cameras_down = int(
        session.scalar(
            select(func.count(Camera.id)).where(
                Camera.status.in_([CameraStatus.OFFLINE.value, CameraStatus.ERROR.value])
            )
        )
        or 0
    )
    cameras_total = int(session.scalar(select(func.count(Camera.id))) or 0)

    return {
        "assets_total": total,
        "assets_assigned": assigned,
        "assets_unassigned": max(0, total - assigned),
        "assets_with_technician": with_technician,
        "assets_in_repair": in_repair,
        "assets_removed": removed,
        "pending_asset_events": pending_movements,
        "movements_total": total_movements,
        "cameras_total": cameras_total,
        "cameras_online": cameras_online,
        "cameras_down": cameras_down,
    }


def technicians(session: Session) -> list[User]:
    return list(
        session.scalars(
            select(User).where(User.role == Role.TECHNICIAN.value).order_by(User.username)
        ).all()
    )


def employees(session: Session) -> Sequence[Employee]:
    return list(
        session.scalars(select(Employee).where(Employee.is_active.is_(True)).order_by(Employee.first_name)).all()
    )
