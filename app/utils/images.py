"""Image helpers shared by the API, scripts and the camera pipeline."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import cv2
import numpy as np

ALLOWED_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp"}
MAX_UPLOAD_BYTES = 8 * 1024 * 1024


def decode_image(data: bytes) -> Optional[np.ndarray]:
    """Decode raw upload bytes into a BGR image (None when unreadable)."""
    if not data:
        return None
    buffer = np.frombuffer(data, dtype=np.uint8)
    image = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    return image


def encode_jpeg(image: np.ndarray, quality: int = 85) -> bytes:
    ok, buffer = cv2.imencode(".jpg", image, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
    if not ok:  # pragma: no cover - depends on codec
        raise ValueError("JPEG kodlashda xatolik")
    return buffer.tobytes()


def save_jpeg(path: Path, image: np.ndarray, quality: int = 85) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(encode_jpeg(image, quality))
    return path


def validate_upload(filename: str, data: bytes) -> Optional[str]:
    """Return an Uzbek error message, or None when the upload looks acceptable."""
    if not filename:
        return "Fayl nomi bo‘sh."
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_IMAGE_SUFFIXES:
        return "Faqat JPG, PNG yoki WEBP rasmlar qabul qilinadi."
    if len(data) == 0:
        return "Fayl bo‘sh."
    if len(data) > MAX_UPLOAD_BYTES:
        return "Rasm hajmi 8 MB dan oshmasligi kerak."
    return None


def image_quality(image: np.ndarray) -> tuple[float, list[str]]:
    """Heuristic quality score in [0, 1] plus human readable warnings.

    Combines sharpness (variance of the Laplacian), size and exposure. It is a
    screening aid for enrolment photos, not a biometric quality standard.
    """
    warnings: list[str] = []
    if image is None or image.size == 0:
        return 0.0, ["Rasm o‘qilmadi."]

    height, width = image.shape[:2]
    if min(height, width) < 160:
        warnings.append("Rasm juda kichik (kamida 160x160 piksel kerak).")

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    sharpness = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    sharp_score = min(1.0, sharpness / 300.0)
    if sharpness < 40:
        warnings.append("Rasm xira bo‘lishi mumkin (fokus tekshirilsin).")

    brightness = float(gray.mean())
    exposure_ok = 45 <= brightness <= 215
    if brightness < 45:
        warnings.append("Rasm juda qorong‘i.")
    elif brightness > 215:
        warnings.append("Rasm juda yorug‘ (yorug‘lik oshib ketgan).")
    exposure_score = 1.0 if exposure_ok else 0.5

    size_score = min(1.0, min(height, width) / 480.0)
    score = round(0.55 * sharp_score + 0.25 * exposure_score + 0.20 * size_score, 3)
    return score, warnings


def ensure_within(base: Path, candidate: Path) -> Path:
    """Resolve *candidate* and make sure it stays inside *base*."""
    base_resolved = base.resolve()
    target = candidate.resolve()
    if base_resolved != target and base_resolved not in target.parents:
        raise ValueError("Fayl ruxsat etilgan katalogdan tashqarida.")
    return target


def relative_to_data(path: Path, data_dir: Path) -> str:
    try:
        return path.resolve().relative_to(data_dir.resolve()).as_posix()
    except ValueError:
        return path.name


def resolve_data_path(relative: str, data_dir: Path) -> Path:
    return ensure_within(data_dir, data_dir / relative)


def read_bytes_preview(image: np.ndarray, max_width: int = 320) -> bytes:
    height, width = image.shape[:2]
    if width > max_width:
        scale = max_width / float(width)
        image = cv2.resize(image, (max_width, int(height * scale)), interpolation=cv2.INTER_AREA)
    return encode_jpeg(image, 80)


def padded_crop(image: np.ndarray, box, margin: float = 0.45) -> Optional[np.ndarray]:
    """Crop *box* with a relative margin, clamped to the frame.

    Used by the "camera snapshot" enrolment: the face box alone is too tight for
    InsightFace, a bit of context keeps detection stable.
    """
    if image is None or image.size == 0:
        return None
    height, width = image.shape[:2]
    box_width = float(box.x2) - float(box.x1)
    box_height = float(box.y2) - float(box.y1)
    if box_width < 1 or box_height < 1:
        return None
    pad_x = box_width * margin
    pad_y = box_height * margin
    x1 = max(0, int(float(box.x1) - pad_x))
    y1 = max(0, int(float(box.y1) - pad_y))
    x2 = min(width, int(float(box.x2) + pad_x))
    y2 = min(height, int(float(box.y2) + pad_y))
    if x2 - x1 < 32 or y2 - y1 < 32:
        return None
    return image[y1:y2, x1:x2].copy()
