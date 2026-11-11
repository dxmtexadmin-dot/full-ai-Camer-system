"""Equipment category registry and individually registered physical assets."""

from __future__ import annotations

from datetime import date, datetime
from typing import List, Optional

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.enums import EquipmentStatus
from app.models.user import utcnow


class EquipmentCategory(Base):
    """Detection class -> business category mapping.

    ``yolo_labels`` holds the class names that the deployed model emits; the
    mapping is configurable so a re-trained model can be adopted without code
    changes.
    """

    __tablename__ = "equipment_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)
    label_uz: Mapped[str] = mapped_column(String(120), nullable=False)
    yolo_labels: Mapped[list] = mapped_column(JSON, default=list)
    is_person: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    min_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=100)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    assets: Mapped[List["EquipmentAsset"]] = relationship(back_populates="category")

    def matches(self, yolo_label: str) -> bool:
        return yolo_label.strip().lower() in {str(item).strip().lower() for item in (self.yolo_labels or [])}


class EquipmentAsset(Base):
    """A specific physical unit (e.g. monitor MON-001 with a serial number).

    The QR-inventory module extends the original registry instead of creating a
    parallel one: ``asset_code`` is the inventory number an operator prints on
    the label, and the responsibility fields below are filled by
    ``assignment_service``. All new columns are nullable or defaulted so the
    table can be migrated in place on a live database.
    """

    __tablename__ = "equipment_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    asset_code: Mapped[str] = mapped_column(String(48), unique=True, nullable=False, index=True)
    category_id: Mapped[int] = mapped_column(
        ForeignKey("equipment_categories.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    brand: Mapped[str] = mapped_column(String(80), default="")
    model: Mapped[str] = mapped_column(String(120), default="")
    serial_number: Mapped[Optional[str]] = mapped_column(String(120), nullable=True, index=True)
    asset_tag: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    location: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(24), default=EquipmentStatus.IN_STOCK.value)
    photo_path: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    # -- QR inventory fields (added by the asset module, safe to leave empty) --
    name: Mapped[str] = mapped_column(String(160), default="")
    room: Mapped[str] = mapped_column(String(80), default="")
    department: Mapped[str] = mapped_column(String(120), default="")
    responsible_employee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    # Snapshot of the F.I.Sh. at assignment time: it stays readable even if the
    # employee record is later renamed or deactivated.
    responsible_name: Mapped[str] = mapped_column(String(160), default="")
    responsible_position: Mapped[str] = mapped_column(String(120), default="")
    technician_employee_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("employees.id", ondelete="SET NULL"), nullable=True, index=True
    )
    technician_name: Mapped[str] = mapped_column(String(160), default="")
    purchase_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    category: Mapped[EquipmentCategory] = relationship(back_populates="assets", lazy="joined")

    @property
    def status_label_uz(self) -> str:
        try:
            return EquipmentStatus(self.status).label_uz
        except ValueError:
            return self.status

    @property
    def display_name(self) -> str:
        """Device name for labels; falls back to the category name."""
        if self.name:
            return self.name
        if self.category is not None:
            return self.category.label_uz
        return self.asset_code

    @property
    def full_model(self) -> str:
        return " ".join(part for part in (self.brand, self.model) if part).strip()

    def __repr__(self) -> str:  # pragma: no cover
        return f"<EquipmentAsset {self.asset_code} ({self.category.code if self.category else '?'})>"
