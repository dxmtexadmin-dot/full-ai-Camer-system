"""Camera configuration and runtime status."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, Float, Integer, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database import Base
from app.models.enums import CameraStatus, SourceType
from app.models.user import utcnow


class Camera(Base):
    __tablename__ = "cameras"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    location: Mapped[str] = mapped_column(String(120), default="")
    source_type: Mapped[str] = mapped_column(String(16), default=SourceType.RTSP.value)
    # RTSP URL / device index / video path. May contain credentials -> never
    # returned raw by the API, always masked.
    source: Mapped[str] = mapped_column(Text, default="")

    is_enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # Virtual entrance/exit line, in source-frame pixel coordinates.
    line_x1: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    line_y1: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    line_x2: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    line_y2: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    settings: Mapped[dict] = mapped_column(JSON, default=dict)

    status: Mapped[str] = mapped_column(String(16), default=CameraStatus.UNKNOWN.value, index=True)
    status_message: Mapped[str] = mapped_column(Text, default="")
    last_frame_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_connected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, default=0)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    @property
    def has_line(self) -> bool:
        return None not in (self.line_x1, self.line_y1, self.line_x2, self.line_y2)

    @property
    def line(self) -> Optional[tuple[tuple[float, float], tuple[float, float]]]:
        if not self.has_line:
            return None
        return (
            (float(self.line_x1), float(self.line_y1)),
            (float(self.line_x2), float(self.line_y2)),
        )

    @property
    def status_enum(self) -> CameraStatus:
        try:
            return CameraStatus(self.status)
        except ValueError:
            return CameraStatus.UNKNOWN

    @property
    def status_label_uz(self) -> str:
        return self.status_enum.label_uz

    @property
    def masked_source(self) -> str:
        """Hide credentials before showing the address in the UI."""
        if not self.source:
            return ""
        raw = self.source
        if "://" in raw:
            scheme, rest = raw.split("://", 1)
            if "@" in rest:
                credentials, host = rest.split("@", 1)
                user = credentials.split(":", 1)[0]
                return f"{scheme}://{user}:***@{host}"
            return raw
        return raw

    @property
    def confidence_override(self) -> Optional[float]:
        value = (self.settings or {}).get("confidence")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Camera {self.code} {self.status}>"
