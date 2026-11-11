"""QR inventory tables: codes, assignments, usage history, movements, repairs.

Design notes
------------
* The physical device itself stays in ``equipment_assets`` (the original
  registry). These tables only describe *relationships* and *facts* around it,
  so nothing that already works has to be rewritten.
* Every table is append-mostly: history rows are never deleted and corrections
  are added as new rows (``is_correction`` / ``corrects_id``).
* ``AssetQrCode`` keeps the printable token, never the asset id: scanning a
  label must not reveal a guessable internal number.
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import (
    AssetMovementType,
    AssignmentKind,
    RepairStatus,
    UsageAction,
    VerificationStatus,
)
from app.models.user import utcnow


class AssetQrCode(Base):
    """One printable QR token per asset (re-issued only by an administrator)."""

    __tablename__ = "asset_qr_codes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_assets.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    # URL-safe random token; the QR payload is "<base>/qr/<token>".
    token: Mapped[str] = mapped_column(String(64), nullable=False, unique=True, index=True)
    label_size: Mapped[str] = mapped_column(String(32), default="30x30")
    reprint_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    issued_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    issued_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    last_printed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    asset = relationship("EquipmentAsset", lazy="joined")


class AssetAssignment(Base):
    """Who is responsible for the asset right now, and who was before.

    Only one row per asset may be open with ``kind=RESPONSIBLE``; the service
    enforces it and ``Index`` below keeps the lookup cheap.
    """

    __tablename__ = "asset_assignments"
    __table_args__ = (
        Index("ix_asset_assignment_open", "asset_id", "kind", "ended_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Snapshot so history stays readable after an employee is renamed.
    employee_name: Mapped[str] = mapped_column(String(160), default="")
    employee_position: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(24), default=AssignmentKind.RESPONSIBLE.value, nullable=False)
    room: Mapped[str] = mapped_column(String(80), default="")
    department: Mapped[str] = mapped_column(String(120), default="")
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    assigned_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_by_username: Mapped[str] = mapped_column(String(64), default="")
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    ended_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    end_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    asset = relationship("EquipmentAsset", lazy="joined")

    @property
    def is_open(self) -> bool:
        return self.ended_at is None

    @property
    def kind_label_uz(self) -> str:
        try:
            return AssignmentKind(self.kind).label_uz
        except ValueError:
            return self.kind


class AssetUsageRecord(Base):
    """Append-only "who used it, when" journal for one device."""

    __tablename__ = "asset_usage_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    employee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    employee_name: Mapped[str] = mapped_column(String(160), default="")
    action: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_username: Mapped[str] = mapped_column(String(64), default="")
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    camera_code: Mapped[str] = mapped_column(String(32), default="")
    assignment_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("asset_assignments.id", ondelete="SET NULL"), nullable=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # A fix is a new row that points at the row it corrects; nothing is edited.
    is_correction: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    corrects_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("asset_usage_history.id", ondelete="SET NULL"), nullable=True
    )

    asset = relationship("EquipmentAsset", lazy="joined")

    @property
    def action_label_uz(self) -> str:
        try:
            return UsageAction(self.action).label_uz
        except ValueError:
            return self.action


class AssetMovementEvent(Base):
    """Camera observation about a specific device (or an unidentified one)."""

    __tablename__ = "asset_movement_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("equipment_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    asset_code: Mapped[str] = mapped_column(String(48), default="", index=True)
    camera_code: Mapped[str] = mapped_column(String(32), default="", index=True)
    movement_type: Mapped[str] = mapped_column(String(24), default=AssetMovementType.UNIDENTIFIED.value)
    detected_category: Mapped[Optional[str]] = mapped_column(String(48), nullable=True, index=True)
    qr_token: Mapped[Optional[str]] = mapped_column(String(64), nullable=True, index=True)
    confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    verification_status: Mapped[str] = mapped_column(
        String(16), default=VerificationStatus.PENDING.value, index=True
    )
    evidence_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)

    @property
    def movement_label_uz(self) -> str:
        try:
            return AssetMovementType(self.movement_type).label_uz
        except ValueError:
            return self.movement_type


class AssetCameraLink(Base):
    """Which camera watches which device (used to attach observations)."""

    __tablename__ = "asset_camera_links"
    __table_args__ = (UniqueConstraint("asset_id", "camera_code", name="uq_asset_camera"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    camera_code: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    zone: Mapped[str] = mapped_column(String(120), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TechnicianAssignment(Base):
    """Service technician looking after a device."""

    __tablename__ = "technician_assignments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    technician_employee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    technician_name: Mapped[str] = mapped_column(String(160), default="")
    assigned_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


class AssetRepairRecord(Base):
    """One repair visit, linked to the inventory number through the asset."""

    __tablename__ = "asset_repair_records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_assets.id", ondelete="CASCADE"), nullable=False, index=True
    )
    technician_employee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    technician_name: Mapped[str] = mapped_column(String(160), default="")
    reason: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(16), default=RepairStatus.OPEN.value, index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    returned_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    asset = relationship("EquipmentAsset", lazy="joined")

    @property
    def status_label_uz(self) -> str:
        try:
            return RepairStatus(self.status).label_uz
        except ValueError:
            return self.status

    @property
    def is_open(self) -> bool:
        return self.status in {RepairStatus.OPEN.value, RepairStatus.IN_PROGRESS.value}


class AssetAuditLog(Base):
    """Per-asset audit trail (immutable append-only)."""

    __tablename__ = "asset_audit_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("equipment_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    actor_username: Mapped[str] = mapped_column(String(64), default="system")
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_correction: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    corrects_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
