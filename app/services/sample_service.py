"""Teaching samples: example photos per equipment category.

The YOLO model shipped with the project (``yolov8s.pt``) only knows the 80 COCO
classes, so a white mouse held in a hand is regularly reported as a smartphone.
The only lasting fix is to teach the model with frames from *this* office. This
service keeps those frames on disk - one folder per category - so they can be
labelled and handed to ``scripts/train_yolo.py``.

Layout::

    data/samples/mouse/mouse_001.jpg
    data/samples/smartphone/smartphone_001.jpg

Files are always re-encoded as JPEG and de-duplicated by content, so uploading
the same picture twice costs nothing.
"""

from __future__ import annotations

import hashlib
import logging
import re
import zipfile
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from typing import Iterable, Optional

import numpy as np
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.services import audit_service, equipment_service
from app.utils.images import decode_image, encode_jpeg, ensure_within, validate_upload

logger = logging.getLogger(__name__)

SAMPLES_DIRNAME = "samples"
# What the manual recommends before a retrain is worth the CPU time.
MIN_PER_CATEGORY = 10
TARGET_PER_CATEGORY = 15
MAX_PER_CATEGORY = 80
MAX_FILES_PER_UPLOAD = 40
JPEG_QUALITY = 92

_CODE_RE = re.compile(r"^[a-z0-9_]{1,48}$")
_NAME_RE = re.compile(r"^[a-z0-9_\-]{1,64}\.jpg$")


class SampleError(ValueError):
    """Raised for an unknown category or an unsafe file name."""


@dataclass(frozen=True)
class SampleResult:
    stored: int = 0
    duplicates: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.stored > 0


@dataclass(frozen=True)
class CategoryProgress:
    code: str
    label_uz: str
    count: int

    @property
    def target(self) -> int:
        return TARGET_PER_CATEGORY

    @property
    def ready(self) -> bool:
        return self.count >= MIN_PER_CATEGORY

    @property
    def percent(self) -> int:
        return min(100, round(100 * self.count / TARGET_PER_CATEGORY))

    @property
    def missing(self) -> int:
        return max(0, TARGET_PER_CATEGORY - self.count)


# -- paths --------------------------------------------------------------------
def samples_root(settings: Optional[Settings] = None) -> Path:
    settings = settings or get_settings()
    return Path(settings.data_dir) / SAMPLES_DIRNAME


def _safe_code(code: str) -> str:
    value = (code or "").strip().lower()
    if not _CODE_RE.match(value):
        raise SampleError("Toifa kodi noto‘g‘ri.")
    return value


def category_dir(settings: Optional[Settings] = None, code: str = "", *, create: bool = False) -> Path:
    root = samples_root(settings)
    directory = ensure_within(root, root / _safe_code(code))
    if create:
        directory.mkdir(parents=True, exist_ok=True)
    return directory


def list_samples(settings: Optional[Settings] = None, code: str = "") -> list[Path]:
    directory = category_dir(settings, code)
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.glob("*.jpg") if path.is_file())


def count(settings: Optional[Settings] = None, code: str = "") -> int:
    return len(list_samples(settings, code))


def _digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _existing_digests(directory: Path) -> set[str]:
    digests = set()
    for path in directory.glob("*.jpg"):
        try:
            digests.add(_digest(path.read_bytes()))
        except OSError:  # pragma: no cover - unreadable file
            continue
    return digests


def _next_index(directory: Path) -> int:
    highest = 0
    for path in directory.glob("*.jpg"):
        match = re.search(r"_(\d{3,})$", path.stem)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


# -- writing ------------------------------------------------------------------
def add_images(
    settings: Optional[Settings],
    code: str,
    uploads: Iterable[tuple[str, bytes]],
    *,
    user=None,
    session: Optional[Session] = None,
) -> SampleResult:
    """Store uploaded pictures for *code*; returns counts and Uzbek errors."""
    directory = category_dir(settings, code, create=True)
    stored = duplicates = 0
    errors: list[str] = []
    seen = _existing_digests(directory)
    index = _next_index(directory)

    for position, (filename, data) in enumerate(uploads):
        if position >= MAX_FILES_PER_UPLOAD:
            errors.append(f"Bir marta ko‘pi bilan {MAX_FILES_PER_UPLOAD} ta rasm yuklanadi.")
            break
        message = validate_upload(filename, data)
        if message:
            errors.append(f"{filename}: {message}")
            continue
        image = decode_image(data)
        if image is None or image.size == 0:
            errors.append(f"{filename}: rasm o‘qilmadi (buzilgan fayl).")
            continue
        if index > MAX_PER_CATEGORY:
            errors.append(f"«{code}» uchun chegaraga yetdi ({MAX_PER_CATEGORY} rasm).")
            break

        payload = encode_jpeg(image, JPEG_QUALITY)
        digest = _digest(payload)
        if digest in seen:
            duplicates += 1
            continue
        seen.add(digest)

        target = directory / f"{_safe_code(code)}_{index:03d}.jpg"
        target.write_bytes(payload)
        index += 1
        stored += 1

    if stored and session is not None:
        audit_service.record(
            session,
            action="samples.add",
            entity="category",
            entity_id=code,
            details={"stored": stored, "duplicates": duplicates, "errors": len(errors)},
            user=user,
            username="admin",
        )
    logger.info("Namuna rasmlar: %s +%s (dublikat %s)", code, stored, duplicates)
    return SampleResult(stored=stored, duplicates=duplicates, errors=errors)


def add_frame(
    settings: Optional[Settings],
    code: str,
    frame: np.ndarray,
    *,
    user=None,
    session: Optional[Session] = None,
) -> Path:
    """Store one live camera frame as a teaching sample."""
    directory = category_dir(settings, code, create=True)
    payload = encode_jpeg(frame, JPEG_QUALITY)
    digest = _digest(payload)
    if digest in _existing_digests(directory):
        raise SampleError("Bu kadr allaqachon to‘plamda bor.")
    index = _next_index(directory)
    if index > MAX_PER_CATEGORY:
        raise SampleError(f"«{code}» uchun chegaraga yetdi ({MAX_PER_CATEGORY} rasm).")
    target = directory / f"{_safe_code(code)}_{index:03d}.jpg"
    target.write_bytes(payload)
    if session is not None:
        audit_service.record(
            session,
            action="samples.capture",
            entity="category",
            entity_id=code,
            details={"file": target.name},
            user=user,
            username="admin",
        )
    return target


def resolve_sample(settings: Optional[Settings], code: str, name: str) -> Path:
    directory = category_dir(settings, code)
    if not _NAME_RE.match(name or ""):
        raise SampleError("Fayl nomi noto‘g‘ri.")
    path = ensure_within(directory, directory / name)
    if not path.is_file():
        raise SampleError("Rasm topilmadi.")
    return path


def delete_sample(
    settings: Optional[Settings],
    code: str,
    name: str,
    *,
    user=None,
    session: Optional[Session] = None,
) -> bool:
    try:
        path = resolve_sample(settings, code, name)
    except SampleError:
        return False
    try:
        path.unlink()
    except OSError:  # pragma: no cover - locked file
        return False
    if session is not None:
        audit_service.record(
            session,
            action="samples.delete",
            entity="category",
            entity_id=code,
            details={"file": name},
            user=user,
            username="admin",
        )
    return True


def clear_category(settings: Optional[Settings], code: str) -> int:
    removed = 0
    for path in list_samples(settings, code):
        try:
            path.unlink()
            removed += 1
        except OSError:  # pragma: no cover
            continue
    return removed


# -- reporting ----------------------------------------------------------------
def progress(session: Session, settings: Optional[Settings] = None) -> list[CategoryProgress]:
    settings = settings or get_settings()
    rows: list[CategoryProgress] = []
    for category in equipment_service.categories(session):
        if category.is_person:
            continue
        rows.append(
            CategoryProgress(
                code=category.code,
                label_uz=category.label_uz,
                count=count(settings, category.code),
            )
        )
    rows.sort(key=lambda item: (-item.count, item.code))
    return rows


def totals(rows: Iterable[CategoryProgress]) -> dict:
    rows = list(rows)
    return {
        "files": sum(item.count for item in rows),
        "ready": sum(1 for item in rows if item.ready),
        "started": sum(1 for item in rows if item.count),
        "categories": len(rows),
        "target": TARGET_PER_CATEGORY,
        "minimum": MIN_PER_CATEGORY,
    }


def zip_samples(settings: Optional[Settings], code: Optional[str] = None) -> bytes:
    """Bundle samples (one category or all) into a ZIP for labelling tools."""
    root = samples_root(settings)
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        codes = [_safe_code(code)] if code else [path.name for path in root.glob("*") if path.is_dir()]
        for current in sorted(codes):
            for path in list_samples(settings, current):
                archive.write(path, arcname=f"{current}/{path.name}")
    return buffer.getvalue()
