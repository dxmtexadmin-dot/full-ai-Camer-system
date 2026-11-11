"""String enums shared by models, services and templates."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - convenience for templates
        return self.value


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    TECHNICIAN = "technician"
    VIEWER = "viewer"

    @property
    def label_uz(self) -> str:
        return {
            Role.ADMIN: "Administrator",
            Role.OPERATOR: "Operator",
            Role.TECHNICIAN: "Texnik",
            Role.VIEWER: "Kuzatuvchi",
        }[self]


class EventType(StrEnum):
    PERSON_ENTERED = "PERSON_ENTERED"
    PERSON_EXITED = "PERSON_EXITED"
    UNKNOWN_PERSON_ENTERED = "UNKNOWN_PERSON_ENTERED"
    UNKNOWN_PERSON_EXITED = "UNKNOWN_PERSON_EXITED"
    EQUIPMENT_BROUGHT_IN = "EQUIPMENT_BROUGHT_IN"
    EQUIPMENT_TAKEN_OUT = "EQUIPMENT_TAKEN_OUT"
    EQUIPMENT_RETURNED = "EQUIPMENT_RETURNED"
    EQUIPMENT_PICKED_UP = "EQUIPMENT_PICKED_UP"
    EQUIPMENT_PUT_DOWN = "EQUIPMENT_PUT_DOWN"
    INVENTORY_DISCREPANCY = "INVENTORY_DISCREPANCY"
    CAMERA_DISCONNECTED = "CAMERA_DISCONNECTED"
    CAMERA_RECONNECTED = "CAMERA_RECONNECTED"
    UNKNOWN_PERSON_DETECTED = "UNKNOWN_PERSON_DETECTED"
    REQUIRES_REVIEW = "REQUIRES_REVIEW"

    @property
    def label_uz(self) -> str:
        return EVENT_LABELS_UZ.get(self, self.value)


EVENT_LABELS_UZ: dict[EventType, str] = {
    EventType.PERSON_ENTERED: "Xodim kirdi",
    EventType.PERSON_EXITED: "Xodim chiqdi",
    EventType.UNKNOWN_PERSON_ENTERED: "Noma'lum shaxs kirdi",
    EventType.UNKNOWN_PERSON_EXITED: "Noma'lum shaxs chiqdi",
    EventType.EQUIPMENT_BROUGHT_IN: "Jihoz olib kirildi",
    EventType.EQUIPMENT_TAKEN_OUT: "Jihoz olib chiqildi",
    EventType.EQUIPMENT_RETURNED: "Jihoz qaytarildi",
    EventType.EQUIPMENT_PICKED_UP: "Jihoz olindi (biriktirildi)",
    EventType.EQUIPMENT_PUT_DOWN: "Jihoz qo‘yildi (biriktirish yopildi)",
    EventType.INVENTORY_DISCREPANCY: "Inventar farqi",
    EventType.CAMERA_DISCONNECTED: "Kamera uzildi",
    EventType.CAMERA_RECONNECTED: "Kamera tiklandi",
    EventType.UNKNOWN_PERSON_DETECTED: "Noma'lum shaxs aniqlandi",
    EventType.REQUIRES_REVIEW: "Tekshirish talab qilinadi",
}

PERSON_EVENT_TYPES = {
    EventType.PERSON_ENTERED,
    EventType.PERSON_EXITED,
    EventType.UNKNOWN_PERSON_ENTERED,
    EventType.UNKNOWN_PERSON_EXITED,
    EventType.UNKNOWN_PERSON_DETECTED,
}

EQUIPMENT_EVENT_TYPES = {
    EventType.EQUIPMENT_BROUGHT_IN,
    EventType.EQUIPMENT_TAKEN_OUT,
    EventType.EQUIPMENT_RETURNED,
}

# Possession lifecycle facts: who picked an object up and when it was put down.
# They describe an observation, not a ledger movement, so they deliberately stay
# out of EQUIPMENT_EVENT_TYPES: pick-up alone must never change the balance.
POSSESSION_EVENT_TYPES = {
    EventType.EQUIPMENT_PICKED_UP,
    EventType.EQUIPMENT_PUT_DOWN,
}

BUSINESS_EVENT_TYPES = PERSON_EVENT_TYPES | EQUIPMENT_EVENT_TYPES | POSSESSION_EVENT_TYPES


class VerificationStatus(StrEnum):
    AUTO = "AUTO"
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"

    @property
    def label_uz(self) -> str:
        return {
            VerificationStatus.AUTO: "Avtomatik",
            VerificationStatus.PENDING: "Tekshirish kerak",
            VerificationStatus.CONFIRMED: "Tasdiqlangan",
            VerificationStatus.REJECTED: "Rad etilgan",
        }[self]


class Direction(StrEnum):
    IN = "IN"
    OUT = "OUT"
    NONE = "NONE"

    @property
    def label_uz(self) -> str:
        return {
            Direction.IN: "Kirim",
            Direction.OUT: "Chiqim",
            Direction.NONE: "Aniqlanmagan",
        }[self]


class SyncStatus(StrEnum):
    PENDING = "PENDING"
    SYNCED = "SYNCED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class TransactionType(StrEnum):
    INITIAL = "INITIAL"
    RECEIVED = "RECEIVED"
    REMOVED = "REMOVED"
    RETURNED = "RETURNED"
    TRANSFER = "TRANSFER"
    CORRECTION = "CORRECTION"

    @property
    def label_uz(self) -> str:
        return {
            TransactionType.INITIAL: "Boshlang‘ich qoldiq",
            TransactionType.RECEIVED: "Kirim",
            TransactionType.REMOVED: "Chiqim",
            TransactionType.RETURNED: "Qaytarildi",
            TransactionType.TRANSFER: "Ichki ko‘chirish",
            TransactionType.CORRECTION: "Qo‘lda tuzatish",
        }[self]


class EquipmentStatus(StrEnum):
    IN_STOCK = "IN_STOCK"
    IN_USE = "IN_USE"
    MAINTENANCE = "MAINTENANCE"
    LOST = "LOST"
    RETIRED = "RETIRED"

    @property
    def label_uz(self) -> str:
        return {
            EquipmentStatus.IN_STOCK: "Omborda",
            EquipmentStatus.IN_USE: "Ishlatilmoqda",
            EquipmentStatus.MAINTENANCE: "Ta'mirda",
            EquipmentStatus.LOST: "Yo‘qolgan",
            EquipmentStatus.RETIRED: "Hisobdan chiqarilgan",
        }[self]


class CameraStatus(StrEnum):
    UNKNOWN = "UNKNOWN"
    CONNECTING = "CONNECTING"
    ONLINE = "ONLINE"
    OFFLINE = "OFFLINE"
    ERROR = "ERROR"
    DISABLED = "DISABLED"

    @property
    def label_uz(self) -> str:
        return {
            CameraStatus.UNKNOWN: "Noma'lum",
            CameraStatus.CONNECTING: "Ulanmoqda",
            CameraStatus.ONLINE: "Onlayn",
            CameraStatus.OFFLINE: "Oflayn",
            CameraStatus.ERROR: "Xatolik",
            CameraStatus.DISABLED: "O‘chirilgan",
        }[self]


class SourceType(StrEnum):
    RTSP = "rtsp"
    USB = "usb"
    FILE = "file"
    SIMULATION = "simulation"


class AssociationQuality(StrEnum):
    STRONG = "STRONG"
    WEAK = "WEAK"
    AMBIGUOUS = "AMBIGUOUS"
    NONE = "NONE"


# ---------------------------------------------------------------------------
# QR inventory module
# ---------------------------------------------------------------------------
class AssignmentKind(StrEnum):
    RESPONSIBLE = "RESPONSIBLE"  # doimiy mas'ul xodim
    TEMPORARY = "TEMPORARY"  # vaqtincha olgan shaxs
    TECHNICIAN = "TECHNICIAN"  # texnik xizmat uchun biriktirilgan

    @property
    def label_uz(self) -> str:
        return {
            AssignmentKind.RESPONSIBLE: "Mas’ul xodim",
            AssignmentKind.TEMPORARY: "Vaqtinchalik foydalanuvchi",
            AssignmentKind.TECHNICIAN: "Texnik xizmat",
        }[self]


class RepairStatus(StrEnum):
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"
    RETURNED = "RETURNED"
    CANCELLED = "CANCELLED"

    @property
    def label_uz(self) -> str:
        return {
            RepairStatus.OPEN: "Qabul qilindi",
            RepairStatus.IN_PROGRESS: "Ta’mirlanmoqda",
            RepairStatus.DONE: "Ta’mirlandi",
            RepairStatus.RETURNED: "Qaytarildi",
            RepairStatus.CANCELLED: "Bekor qilindi",
        }[self]


class AssetMovementType(StrEnum):
    TAKEN_OUT = "TAKEN_OUT"
    RETURNED = "RETURNED"
    MOVED = "MOVED"
    QR_SCANNED = "QR_SCANNED"
    UNIDENTIFIED = "UNIDENTIFIED"

    @property
    def label_uz(self) -> str:
        return {
            AssetMovementType.TAKEN_OUT: "Joyidan olib chiqildi",
            AssetMovementType.RETURNED: "Joyiga qaytarildi",
            AssetMovementType.MOVED: "Joyi o‘zgartirildi",
            AssetMovementType.QR_SCANNED: "QR kod o‘qildi",
            AssetMovementType.UNIDENTIFIED: "Aniq identifikatsiya qilinmagan",
        }[self]


class UsageAction(StrEnum):
    CREATED = "CREATED"
    UPDATED = "UPDATED"
    ASSIGNED = "ASSIGNED"
    TRANSFERRED = "TRANSFERRED"
    RELEASED = "RELEASED"
    TEMPORARY_TAKEN = "TEMPORARY_TAKEN"
    TEMPORARY_RETURNED = "TEMPORARY_RETURNED"
    REPAIR_RECEIVED = "REPAIR_RECEIVED"
    REPAIR_FINISHED = "REPAIR_FINISHED"
    REPAIR_RETURNED = "REPAIR_RETURNED"
    QR_ISSUED = "QR_ISSUED"
    QR_SCANNED = "QR_SCANNED"
    COMMENT = "COMMENT"
    CORRECTION = "CORRECTION"

    @property
    def label_uz(self) -> str:
        return {
            UsageAction.CREATED: "Ro‘yxatga olindi",
            UsageAction.UPDATED: "Ma’lumot yangilandi",
            UsageAction.ASSIGNED: "Xodimga biriktirildi",
            UsageAction.TRANSFERRED: "Boshqa xodimga o‘tkazildi",
            UsageAction.RELEASED: "Biriktirish yopildi",
            UsageAction.TEMPORARY_TAKEN: "Vaqtincha olindi",
            UsageAction.TEMPORARY_RETURNED: "Vaqtincha qaytarildi",
            UsageAction.REPAIR_RECEIVED: "Ta’mirga qabul qilindi",
            UsageAction.REPAIR_FINISHED: "Ta’mir yakunlandi",
            UsageAction.REPAIR_RETURNED: "Ta’mirdan qaytarildi",
            UsageAction.QR_ISSUED: "QR kod yaratildi",
            UsageAction.QR_SCANNED: "QR skanerlandi",
            UsageAction.COMMENT: "Izoh qo‘shildi",
            UsageAction.CORRECTION: "Tuzatish kiritildi",
        }[self]
