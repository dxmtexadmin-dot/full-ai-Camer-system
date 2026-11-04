"""Event log, inventory ledger and Google Sheets sync log."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import (
    Direction,
    EventType,
    SyncStatus,
    VerificationStatus,
)
from app.models.user import utcnow


class Event(Base):
    """A single observable fact (entry, exit, equipment movement, outage...).

    AI-produced rows always carry confidences and a verification status so the
    dashboard can distinguish facts from candidates.
    """

    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    public_id: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(40), nullable=False, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    camera_code: Mapped[str] = mapped_column(String(32), default="", index=True)

    employee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    person_track_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    asset_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("equipment_assets.id", ondelete="SET NULL"), nullable=True
    )
    equipment_category: Mapped[Optional[str]] = mapped_column(String(48), nullable=True, index=True)
    object_track_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    direction: Mapped[str] = mapped_column(String(8), default=Direction.NONE.value)
    detection_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    face_match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    association_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    verification_status: Mapped[str] = mapped_column(
        String(16), default=VerificationStatus.PENDING.value, index=True
    )
    evidence_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    sheets_sync_status: Mapped[str] = mapped_column(String(16), default=SyncStatus.PENDING.value, index=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    extra: Mapped[dict] = mapped_column(JSON, default=dict)

    dedupe_key: Mapped[str] = mapped_column(String(160), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    reviewed_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    review_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    employee = relationship("Employee", lazy="joined")
    asset = relationship("EquipmentAsset", lazy="joined")
    reviewed_by = relationship("User", lazy="joined")

    @property
    def type_enum(self) -> EventType:
        return EventType(self.event_type)

    @property
    def type_label_uz(self) -> str:
        try:
            return EventType(self.event_type).label_uz
        except ValueError:
            return self.event_type

    @property
    def status_enum(self) -> VerificationStatus:
        try:
            return VerificationStatus(self.verification_status)
        except ValueError:
            return VerificationStatus.PENDING

    @property
    def status_label_uz(self) -> str:
        return self.status_enum.label_uz

    @property
    def direction_label_uz(self) -> str:
        try:
            return Direction(self.direction).label_uz
        except ValueError:
            return self.direction

    @property
    def is_pending(self) -> bool:
        return self.verification_status == VerificationStatus.PENDING.value

    @property
    def employee_label(self) -> str:
        return self.employee.full_name if self.employee else "Noma'lum shaxs"

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Event {self.public_id} {self.event_type} {self.verification_status}>"


class InventoryTransaction(Base):
    """Append-only inventory ledger.

    ``quantity`` is a signed delta applied to the category balance. Only rows
    with ``is_confirmed=True`` and a ledger-affecting type contribute to the
    official balance. Vision candidates are stored unconfirmed and stay out of
    the balance until an operator confirms them.

    Rows are never deleted. When an operator rejects the event behind a
    candidate, the row is kept for the audit trail and flagged
    ``is_rejected=True`` so it stops counting as a pending movement.
    """

    __tablename__ = "inventory_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_code: Mapped[str] = mapped_column(String(48), nullable=False, index=True)
    asset_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("equipment_assets.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("events.id", ondelete="SET NULL"), nullable=True, index=True
    )
    transaction_type: Mapped[str] = mapped_column(String(20), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    location_from: Mapped[str] = mapped_column(String(120), default="")
    location_to: Mapped[str] = mapped_column(String(120), default="")
    is_confirmed: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False, index=True)
    is_rejected: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False, index=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")  # manual | vision | system
    reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_by_user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    asset = relationship("EquipmentAsset", lazy="joined")
    created_by = relationship("User", lazy="joined")

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"<InventoryTransaction {self.category_code} {self.quantity:+d} "
            f"confirmed={self.is_confirmed} rejected={self.is_rejected}>"
        )


class SyncLog(Base):
    """Result of one Google Sheets synchronisation attempt."""

    __tablename__ = "sync_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default=SyncStatus.PENDING.value)
    pushed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    triggered_by: Mapped[str] = mapped_column(String(64), default="scheduler")
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
