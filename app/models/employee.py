"""Employee registry and biometric face templates.

Face embeddings are stored as float32 blobs in the database (never in a public
directory and never returned by the HTTP API).
"""

from __future__ import annotations

from datetime import datetime
from typing import List, Optional

import numpy as np
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base
from app.models.user import utcnow


class Employee(Base):
    __tablename__ = "employees"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_code: Mapped[str] = mapped_column(String(32), unique=True, nullable=False, index=True)
    first_name: Mapped[str] = mapped_column(String(80), nullable=False)
    last_name: Mapped[str] = mapped_column(String(80), nullable=False)
    department: Mapped[str] = mapped_column(String(120), default="")
    employee_number: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    embeddings: Mapped[List["FaceEmbedding"]] = relationship(
        back_populates="employee", cascade="all, delete-orphan", lazy="selectin"
    )

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()

    @property
    def reference_count(self) -> int:
        return len(self.embeddings)

    def __repr__(self) -> str:  # pragma: no cover
        return f"<Employee {self.employee_code} {self.full_name}>"


class FaceEmbedding(Base):
    """One embedding generated from one reference photograph."""

    __tablename__ = "face_embeddings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    employee_id: Mapped[int] = mapped_column(
        ForeignKey("employees.id", ondelete="CASCADE"), nullable=False, index=True
    )
    model_name: Mapped[str] = mapped_column(String(64), default="buffalo_l")
    dimension: Mapped[int] = mapped_column(Integer, default=512)
    quality: Mapped[float] = mapped_column(Float, default=0.0)
    source_image: Mapped[str] = mapped_column(String(512), default="")
    vector: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    employee: Mapped[Employee] = relationship(back_populates="embeddings")

    # --- vector helpers -------------------------------------------------
    @property
    def as_array(self) -> np.ndarray:
        vector = np.frombuffer(self.vector, dtype=np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else vector

    @staticmethod
    def from_ndarray(embedding: np.ndarray) -> bytes:
        vector = np.asarray(embedding, dtype=np.float32).ravel()
        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector = vector / norm
        return vector.tobytes()
