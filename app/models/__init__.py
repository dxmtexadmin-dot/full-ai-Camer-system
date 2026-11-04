"""ORM models.

``Base.metadata`` is populated by importing this package.
"""

from app.models.camera import Camera
from app.models.employee import Employee, FaceEmbedding
from app.models.enums import (
    AssociationQuality,
    CameraStatus,
    Direction,
    EquipmentStatus,
    EventType,
    Role,
    SourceType,
    SyncStatus,
    TransactionType,
    VerificationStatus,
)
from app.models.equipment import EquipmentAsset, EquipmentCategory
from app.models.ledger import Event, InventoryTransaction, SyncLog
from app.models.user import AuditLog, User

__all__ = [
    "Camera",
    "Employee",
    "FaceEmbedding",
    "EquipmentAsset",
    "EquipmentCategory",
    "Event",
    "InventoryTransaction",
    "SyncLog",
    "AuditLog",
    "User",
    "AssociationQuality",
    "CameraStatus",
    "Direction",
    "EquipmentStatus",
    "EventType",
    "Role",
    "SourceType",
    "SyncStatus",
    "TransactionType",
    "VerificationStatus",
]
