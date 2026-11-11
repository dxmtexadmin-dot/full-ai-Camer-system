"""ORM models.

``Base.metadata`` is populated by importing this package.
"""

from app.models.asset import (
    AssetAssignment,
    AssetAuditLog,
    AssetCameraLink,
    AssetMovementEvent,
    AssetQrCode,
    AssetRepairRecord,
    AssetUsageRecord,
    TechnicianAssignment,
)
from app.models.camera import Camera
from app.models.employee import Employee, FaceEmbedding
from app.models.enums import (
    AssetMovementType,
    AssignmentKind,
    AssociationQuality,
    CameraStatus,
    Direction,
    EquipmentStatus,
    EventType,
    RepairStatus,
    Role,
    SourceType,
    SyncStatus,
    TransactionType,
    UsageAction,
    VerificationStatus,
)
from app.models.equipment import EquipmentAsset, EquipmentCategory
from app.models.ledger import Event, InventoryTransaction, SyncLog
from app.models.user import AuditLog, User

__all__ = [
    "AssetAssignment",
    "AssetAuditLog",
    "AssetCameraLink",
    "AssetMovementEvent",
    "AssetQrCode",
    "AssetRepairRecord",
    "AssetUsageRecord",
    "TechnicianAssignment",
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
    "AssetMovementType",
    "AssignmentKind",
    "AssociationQuality",
    "CameraStatus",
    "Direction",
    "EquipmentStatus",
    "EventType",
    "RepairStatus",
    "Role",
    "SourceType",
    "SyncStatus",
    "TransactionType",
    "UsageAction",
    "VerificationStatus",
]
