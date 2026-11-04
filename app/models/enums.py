"""String enums shared by models, services and templates."""

from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover - convenience for templates
        return self.value


class Role(StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"

    @property
    def label_uz(self) -> str:
        return {
            Role.ADMIN: "Administrator",
            Role.OPERATOR: "Operator",
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
