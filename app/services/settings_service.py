"""Runtime configuration editing.

A whitelisted subset of ``.env`` can be edited from the Sozlamalar page. The
file is rewritten atomically, the environment is updated in-process, settings
are reloaded and camera pipelines are restarted. Secrets (APP_SECRET_KEY,
DATABASE_URL, bootstrap password) are intentionally NOT editable from the UI.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.config import ENV_FILE, Settings, get_settings, reload_settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Field:
    key: str
    label_uz: str
    kind: str  # bool | int | float | str
    group: str
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    help_uz: str = ""


FIELDS: list[Field] = [
    Field("YOLO_MODEL_PATH", "YOLO model fayli", "str", "detection", help_uz="Masalan data/models/equipment_best.pt"),
    Field("YOLO_CONFIDENCE", "Deteksiya ishonch chegarasi", "float", "detection", 0.05, 0.95),
    Field("YOLO_IMAGE_SIZE", "Inference o‘lchami (px)", "int", "detection", 320, 1280),
    Field("YOLO_DEDUPE_IOU", "Dublikatlarni bostirish IoU", "float", "detection", 0.1, 0.95),
    Field("PROCESSING_FPS", "Kamera FPS", "int", "camera", 1, 30),
    Field("PROCESSING_WIDTH", "Kadr kengligi", "int", "camera", 320, 3840),
    Field("PROCESSING_HEIGHT", "Kadr balandligi", "int", "camera", 240, 2160),
    Field("CAMERA_RECONNECT_INTERVAL", "Qayta ulanish intervali (s)", "int", "camera", 1, 300),
    Field("SIMULATION_MODE", "Simulyatsiya rejimi", "bool", "camera"),
    Field("FACE_SIMILARITY_THRESHOLD", "Yuz o‘xshashlik chegarasi", "float", "face", 0.1, 0.95),
    Field("FACE_MATCH_MARGIN", "Yuz marjasi (2-xodim farqi)", "float", "face", 0.0, 0.5),
    Field("FACE_RECOGNITION_INTERVAL", "Yuz tanish intervali (kadr)", "int", "face", 1, 120),
    Field("EVENT_COOLDOWN_SECONDS", "Hodisa cooldown (s)", "int", "events", 1, 3600),
    Field("EVIDENCE_ENABLED", "Dalil rasmlarini saqlash", "bool", "events"),
    Field("EVIDENCE_RETENTION_DAYS", "Dalil saqlash muddati (kun)", "int", "events", 1, 3650),
    Field("GOOGLE_SHEETS_ENABLED", "Google Sheets yoqilgan", "bool", "sheets"),
    Field("GOOGLE_SPREADSHEET_ID", "Spreadsheet ID", "str", "sheets"),
    Field("GOOGLE_SERVICE_ACCOUNT_FILE", "Service account JSON yo‘li", "str", "sheets"),
    Field("GOOGLE_SHEETS_SYNC_INTERVAL_SECONDS", "Sinkronizatsiya intervali (s)", "int", "sheets", 15, 86400),
]

FIELD_MAP = {field.key: field for field in FIELDS}


def current_values(settings: Optional[Settings] = None) -> dict[str, str]:
    """Current effective values, as strings ready for a form."""
    settings = settings or get_settings()
    mapping = {
        "YOLO_MODEL_PATH": str(settings.yolo_model_path),
        "YOLO_CONFIDENCE": settings.yolo_confidence,
        "YOLO_IMAGE_SIZE": settings.yolo_image_size,
        "YOLO_DEDUPE_IOU": settings.yolo_dedupe_iou,
        "PROCESSING_FPS": settings.processing_fps,
        "PROCESSING_WIDTH": settings.processing_width,
        "PROCESSING_HEIGHT": settings.processing_height,
        "CAMERA_RECONNECT_INTERVAL": settings.camera_reconnect_interval,
        "SIMULATION_MODE": settings.simulation_mode,
        "FACE_SIMILARITY_THRESHOLD": settings.face_similarity_threshold,
        "FACE_MATCH_MARGIN": settings.face_match_margin,
        "FACE_RECOGNITION_INTERVAL": settings.face_recognition_interval,
        "EVENT_COOLDOWN_SECONDS": settings.event_cooldown_seconds,
        "EVIDENCE_ENABLED": settings.evidence_enabled,
        "EVIDENCE_RETENTION_DAYS": settings.evidence_retention_days,
        "GOOGLE_SHEETS_ENABLED": settings.google_sheets_enabled,
        "GOOGLE_SPREADSHEET_ID": settings.google_spreadsheet_id,
        "GOOGLE_SERVICE_ACCOUNT_FILE": str(settings.google_service_account_file or ""),
        "GOOGLE_SHEETS_SYNC_INTERVAL_SECONDS": settings.google_sync_interval,
    }
    return {key: _to_text(value) for key, value in mapping.items()}


def _to_text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return "" if value is None else str(value)


def _normalise(key: str, raw: str) -> str:
    field = FIELD_MAP.get(key)
    if field is None:
        raise ValueError(f"'{key}' ni tahrirlash mumkin emas.")
    raw = (raw or "").strip()
    if field.kind == "bool":
        return "true" if raw.lower() in {"1", "true", "on", "yes"} else "false"
    if field.kind == "int":
        if raw == "":
            raise ValueError(f"{field.label_uz}: qiymat kiritilishi kerak.")
        try:
            value = int(float(raw))
        except ValueError as exc:
            raise ValueError(f"{field.label_uz}: butun son kerak.") from exc
    elif field.kind == "float":
        if raw == "":
            raise ValueError(f"{field.label_uz}: qiymat kiritilishi kerak.")
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{field.label_uz}: son kerak.") from exc
    else:
        value = raw
    if field.minimum is not None and float(value) < field.minimum:
        raise ValueError(f"{field.label_uz}: kamida {field.minimum}.")
    if field.maximum is not None and float(value) > field.maximum:
        raise ValueError(f"{field.label_uz}: ko‘pi bilan {field.maximum}.")
    return _to_text(value)


def update_env(values: dict[str, str]) -> list[str]:
    """Validate and persist whitelisted values; returns changed keys."""
    cleaned: dict[str, str] = {}
    for key, raw in values.items():
        if key not in FIELD_MAP:
            continue
        normalised = _normalise(key, raw)
        if os.environ.get(key, "") != normalised:
            cleaned[key] = normalised
    if not cleaned:
        return []

    path = Path(ENV_FILE)
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    written: set[str] = set()
    output: list[str] = []
    pattern = re.compile(r"^\s*([A-Z0-9_]+)\s*=")
    for line in lines:
        match = pattern.match(line)
        if match and match.group(1) in cleaned:
            key = match.group(1)
            output.append(f"{key}={cleaned[key]}")
            written.add(key)
        else:
            output.append(line)
    for key, value in cleaned.items():
        if key not in written:
            output.append(f"{key}={value}")

    tmp = path.parent / (path.name + ".tmp")
    tmp.write_text("\n".join(output) + "\n", encoding="utf-8")
    tmp.replace(path)
    # dotenv does not override existing variables, so update os.environ directly.
    for key, value in cleaned.items():
        os.environ[key] = value
    reload_settings()
    logger.warning("Sozlamalar .env faylida yangilandi: %s", ", ".join(sorted(cleaned)))
    return sorted(cleaned)


def refresh_runtime(monitor) -> None:  # noqa: ANN001 - avoids a circular import
    """Push new settings into the long-lived singletons and restart pipelines."""
    settings = get_settings()
    monitor.settings = settings

    yolo = monitor.yolo_service
    if Path(yolo.model_path) != settings.yolo_model_path:
        yolo.model_path = settings.yolo_model_path
        yolo._model = None  # noqa: SLF001 - explicit reload below
        yolo.reload()
    yolo.settings = settings

    monitor.face_service.settings = settings
    monitor.sheets_service.settings = settings

    if monitor.is_started:
        monitor.reload_all()
