"""Face detection and recognition (InsightFace + ONNX Runtime).

Design notes
------------
* The InsightFace app is created once and reused (loading it per frame would be
  unusable on CPU).
* Embeddings are compared against the enrolled templates only. When the best
  match is below the calibrated threshold - or two employees are too close to
  separate - the result is "Noma'lum shaxs" instead of a guess.
* Unknown faces are never enrolled automatically.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

import numpy as np

from app.config import Settings, get_settings
from app.services.types import BBox
from app.utils.images import image_quality

logger = logging.getLogger(__name__)

try:  # pragma: no cover - optional dependency
    from insightface.app import FaceAnalysis  # type: ignore

    _INSIGHTFACE_IMPORT_ERROR: Optional[str] = None
except Exception as exc:  # pragma: no cover
    FaceAnalysis = None  # type: ignore
    _INSIGHTFACE_IMPORT_ERROR = f"insightface o‘rnatilmagan: {exc}"


UNKNOWN_LABEL = "Noma'lum shaxs"


@dataclass
class DetectedFace:
    bbox: BBox
    embedding: np.ndarray
    det_score: float


@dataclass
class FaceMatch:
    employee_id: Optional[int]
    employee_name: Optional[str]
    score: float
    threshold: float
    matched: bool
    reason: str = ""

    @property
    def label(self) -> str:
        return self.employee_name if self.matched and self.employee_name else UNKNOWN_LABEL


@dataclass
class EnrollmentResult:
    embedding: Optional[np.ndarray]
    quality: float
    warnings: list[str] = field(default_factory=list)
    faces_found: int = 0

    @property
    def ok(self) -> bool:
        return self.embedding is not None


@dataclass
class GalleryEntry:
    employee_id: int
    employee_name: str
    vector: np.ndarray


def _l2_normalise(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def match_embeddings(
    query: np.ndarray,
    gallery: Sequence[GalleryEntry],
    *,
    threshold: float,
    margin: float,
) -> FaceMatch:
    """Pure matching logic (unit-testable, no model required).

    Similarity is cosine similarity. Every employee is represented by their best
    reference photo; a match additionally requires a clear gap to the runner-up.
    """
    if query is None or len(gallery) == 0:
        return FaceMatch(None, None, 0.0, threshold, False, "Galereya bo‘sh")

    query = _l2_normalise(np.asarray(query, dtype=np.float32).ravel())
    per_employee: dict[int, tuple[float, str]] = {}
    for entry in gallery:
        vector = _l2_normalise(np.asarray(entry.vector, dtype=np.float32).ravel())
        if vector.shape != query.shape:
            continue
        score = float(np.dot(query, vector))
        current = per_employee.get(entry.employee_id)
        if current is None or score > current[0]:
            per_employee[entry.employee_id] = (score, entry.employee_name)
    if not per_employee:
        return FaceMatch(None, None, 0.0, threshold, False, "Mos o‘lchamdagi embedding yo‘q")

    ranked = sorted(per_employee.items(), key=lambda item: item[1][0], reverse=True)
    best_id, (best_score, best_name) = ranked[0]
    second_score = ranked[1][1][0] if len(ranked) > 1 else -1.0

    if best_score < threshold:
        return FaceMatch(None, None, round(best_score, 4), threshold, False, "O‘xshashlik chegaradan past")
    if second_score > -1.0 and (best_score - second_score) < margin:
        return FaceMatch(
            None,
            None,
            round(best_score, 4),
            threshold,
            False,
            "Ikki xodim bir-biriga juda yaqin - ishonchsiz",
        )
    return FaceMatch(best_id, best_name, round(best_score, 4), threshold, True, "")


class FaceGallery:
    """In-memory snapshot of enrolled embeddings (reloaded on demand)."""

    def __init__(self) -> None:
        self.entries: list[GalleryEntry] = []
        self.version = 0
        self.loaded_at: Optional[str] = None

    def reload(self, session) -> int:
        from sqlalchemy import select

        from app.models import Employee, FaceEmbedding

        rows = session.execute(
            select(Employee.id, Employee.first_name, Employee.last_name, FaceEmbedding.vector, FaceEmbedding.dimension)
            .join(FaceEmbedding, FaceEmbedding.employee_id == Employee.id)
            .where(Employee.is_active.is_(True))
        ).all()
        entries: list[GalleryEntry] = []
        for employee_id, first_name, last_name, blob, _dim in rows:
            vector = np.frombuffer(blob, dtype=np.float32)
            entries.append(
                GalleryEntry(
                    employee_id=int(employee_id),
                    employee_name=f"{first_name} {last_name}".strip(),
                    vector=vector,
                )
            )
        self.entries = entries
        self.version += 1
        from app.utils.time_utils import utc_now

        self.loaded_at = utc_now().isoformat()
        logger.info("Face galereyasi yuklandi: %d embedding", len(entries))
        return len(entries)

    @property
    def size(self) -> int:
        return len(self.entries)


class FaceService:
    """InsightFace wrapper with graceful degradation."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._app = None
        self._lock = threading.Lock()
        self._load_error: Optional[str] = None
        self.gallery = FaceGallery()

    # -- status ---------------------------------------------------------
    @property
    def available(self) -> bool:
        return _INSIGHTFACE_IMPORT_ERROR is None and self._load_error is None

    @property
    def reason_unavailable(self) -> Optional[str]:
        if _INSIGHTFACE_IMPORT_ERROR:
            return _INSIGHTFACE_IMPORT_ERROR
        return self._load_error

    def status(self) -> dict:
        return {
            "available": self.available,
            "reason": self.reason_unavailable,
            "model": self.settings.face_model_name,
            "providers": list(self.settings.face_providers),
            "threshold": self.settings.face_similarity_threshold,
            "margin": self.settings.face_match_margin,
            "gallery_embeddings": self.gallery.size,
            "gallery_loaded_at": self.gallery.loaded_at,
        }

    # -- model lifecycle -------------------------------------------------
    def ensure_loaded(self) -> bool:
        if _INSIGHTFACE_IMPORT_ERROR:
            self._load_error = _INSIGHTFACE_IMPORT_ERROR
            return False
        if self._app is not None:
            return True
        with self._lock:
            if self._app is not None:
                return True
            try:
                logger.info(
                    "InsightFace yuklanmoqda: model=%s provayderlar=%s",
                    self.settings.face_model_name,
                    self.settings.face_providers,
                )
                app = FaceAnalysis(
                    name=self.settings.face_model_name,
                    providers=list(self.settings.face_providers),
                )
                ctx_id = 0 if any("CUDA" in p for p in self.settings.face_providers) else -1
                app.prepare(ctx_id=ctx_id, det_size=self.settings.face_detection_size)
                self._app = app
                self._load_error = None
                return True
            except Exception as exc:  # pragma: no cover - model download/env dependent
                self._load_error = f"InsightFace yuklanmadi: {exc}"
                logger.error(self._load_error)
                return False

    # -- inference -------------------------------------------------------
    def detect_faces(self, image: np.ndarray) -> list[DetectedFace]:
        if not self.ensure_loaded() or self._app is None:
            raise FaceUnavailableError(self.reason_unavailable or "Yuz modeli mavjud emas")
        if image is None or image.size == 0:
            return []
        with self._lock:
            faces = self._app.get(image)
        detected: list[DetectedFace] = []
        for face in faces:
            embedding = getattr(face, "normed_embedding", None)
            if embedding is None:
                embedding = getattr(face, "embedding", None)
            if embedding is None:
                continue
            x1, y1, x2, y2 = [float(v) for v in face.bbox[:4]]
            detected.append(
                DetectedFace(
                    bbox=BBox(x1, y1, x2, y2),
                    embedding=np.asarray(embedding, dtype=np.float32),
                    det_score=float(getattr(face, "det_score", 0.0)),
                )
            )
        return detected

    def recognize(self, image: np.ndarray) -> Optional[FaceMatch]:
        """Recognise the largest face in *image* (typically a person crop)."""
        faces = self.detect_faces(image)
        if not faces:
            return None
        best = max(faces, key=lambda face: face.bbox.area)
        if best.det_score < 0.4:  # very low confidence detection
            return FaceMatch(None, None, 0.0, self.settings.face_similarity_threshold, False, "Yuz sifati past")
        return match_embeddings(
            best.embedding,
            self.gallery.entries,
            threshold=self.settings.face_similarity_threshold,
            margin=self.settings.face_match_margin,
        )

    def create_embedding(self, image: np.ndarray) -> EnrollmentResult:
        """Generate a template for an enrolment photo (largest face wins)."""
        quality, warnings = image_quality(image)
        try:
            faces = self.detect_faces(image)
        except FaceUnavailableError as exc:
            return EnrollmentResult(None, quality, [str(exc)], 0)
        if not faces:
            warnings.append("Rasmda yuz topilmadi.")
            return EnrollmentResult(None, quality, warnings, 0)
        if len(faces) > 1:
            warnings.append("Rasmda bir nechta yuz bor - eng kattasi ishlatildi.")
        best = max(faces, key=lambda face: face.bbox.area)
        if best.det_score < 0.5:
            warnings.append("Yuz ishonchli aniqlanmadi (det_score past).")
        if best.bbox.width < 60 or best.bbox.height < 60:
            warnings.append("Yuz juda kichik - yaxshiroq rasm yuklang.")
        quality = round(min(1.0, 0.5 * quality + 0.5 * min(1.0, best.det_score)), 3)
        return EnrollmentResult(best.embedding, quality, warnings, len(faces))


class FaceUnavailableError(RuntimeError):
    """Raised when face recognition is requested without a working model."""


_FACE_SINGLETON: Optional[FaceService] = None
_FACE_LOCK = threading.Lock()


def get_face_service(settings: Optional[Settings] = None) -> FaceService:
    global _FACE_SINGLETON
    with _FACE_LOCK:
        if _FACE_SINGLETON is None:
            _FACE_SINGLETON = FaceService(settings)
        return _FACE_SINGLETON


def reset_face_service() -> None:
    """Test helper."""
    global _FACE_SINGLETON
    with _FACE_LOCK:
        _FACE_SINGLETON = None


def gallery_from_pairs(pairs: Iterable[tuple[int, str, np.ndarray]]) -> list[GalleryEntry]:
    """Convenience helper for tests/scripts."""
    return [GalleryEntry(employee_id=eid, employee_name=name, vector=np.asarray(vec, dtype=np.float32)) for eid, name, vec in pairs]


def expand_person_crop(frame: np.ndarray, bbox: BBox, top_ratio: float = 0.55) -> Optional[np.ndarray]:
    """Crop the upper part of a person box (where the face usually is)."""
    height, width = frame.shape[:2]
    x1 = max(0, int(bbox.x1))
    x2 = min(width, int(bbox.x2))
    y1 = max(0, int(bbox.y1))
    y2 = min(height, int(bbox.y1 + bbox.height * top_ratio))
    if x2 - x1 < 24 or y2 - y1 < 24:
        return None
    return frame[y1:y2, x1:x2]


def reference_dir(settings: Settings, employee_code: str) -> Path:
    path = settings.employees_dir / employee_code
    path.mkdir(parents=True, exist_ok=True)
    return path
