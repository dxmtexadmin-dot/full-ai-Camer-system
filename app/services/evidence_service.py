"""Evidence stills: storage, restricted resolution and retention."""

from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Optional

import numpy as np

from app.config import Settings, get_settings
from app.utils.images import ensure_within, save_jpeg
from app.utils.time_utils import local_today, utc_now

logger = logging.getLogger(__name__)


def save_evidence(
    frame: np.ndarray,
    public_id: str,
    *,
    settings: Optional[Settings] = None,
    quality: Optional[int] = None,
) -> Optional[str]:
    """Write a still for an event; returns the path relative to the evidence dir."""
    settings = settings or get_settings()
    if not settings.evidence_enabled or frame is None:
        return None
    try:
        day = local_today().isoformat()
        relative = Path(day) / f"{public_id}.jpg"
        target = settings.evidence_dir / relative
        save_jpeg(target, frame, quality or settings.evidence_jpeg_quality)
        return relative.as_posix()
    except Exception as exc:  # pragma: no cover - disk dependent
        logger.error("Dalil rasmini saqlab bo‘lmadi: %s", exc)
        return None


def resolve_evidence(relative_path: str, settings: Optional[Settings] = None) -> Path:
    settings = settings or get_settings()
    return ensure_within(settings.evidence_dir, settings.evidence_dir / relative_path)


def purge_expired(settings: Optional[Settings] = None) -> int:
    """Delete stills older than the configured retention period."""
    settings = settings or get_settings()
    if not settings.evidence_enabled:
        return 0
    cutoff = utc_now() - timedelta(days=settings.evidence_retention_days)
    removed = 0
    if not settings.evidence_dir.exists():
        return 0
    for path in settings.evidence_dir.rglob("*.jpg"):
        try:
            modified = path.stat().st_mtime
            if utc_now().timestamp() - modified > settings.evidence_retention_days * 86400:
                path.unlink()
                removed += 1
        except OSError as exc:  # pragma: no cover - permissions
            logger.warning("Dalil faylini o‘chirib bo‘lmadi (%s): %s", path, exc)
    if removed:
        logger.info("Muddati o‘tgan %d dalil rasmi o‘chirildi (cutoff %s)", removed, cutoff.date())
    return removed
