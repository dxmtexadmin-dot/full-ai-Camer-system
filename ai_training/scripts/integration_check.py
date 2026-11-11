r"""Candidate modelni **ilovaning o'z kodi** bilan mosligini tekshirish.

Bu skript production'ga tegmaydi: `YoloService` ni candidate vaznlar bilan
alohida jarayonda ko'taradi va quyidagilarni tekshiradi:

1. model yuklanadi va 27 ta sinf kodi kutilganidek o'qiladi;
2. `YoloService.detect()` — detektsiya + toifa qoidalari ishlaydi;
3. ByteTrack (`use_tracking=True`) custom sinflar bilan ishlaydi;
4. kadrda `person` topiladi (tizimning tayanchi).

`PASS` bo'lmasa, modelni almashtirish mumkin emas.

Misol
-----
    ai_training\.venv\Scripts\python ai_training\scripts\integration_check.py \
        --weights ai_training\models\<run>\best.pt --images ai_training\dataset\images\val
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def has_person_label(path: Path, labels_dir: Path) -> bool:
    """Kadrda `person` (sinf 0) belgisi bormi — shunday kadrlar birinchi sinaladi."""
    label = labels_dir / f"{path.stem}.txt"
    if not label.is_file():
        return False
    for line in label.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if parts and parts[0] in {"0", "0.0"}:
            return True
    return False


def load_frames(folder: Path, limit: int, labels_dir: Path | None = None) -> list:
    import cv2
    import numpy as np

    paths = [path for path in sorted(folder.iterdir()) if path.suffix.lower() in IMAGE_SUFFIXES]
    if labels_dir is not None:
        paths.sort(key=lambda path: (not has_person_label(path, labels_dir), path.name))

    frames = []
    for path in paths:
        # `cv2.imread` Windows'da kirillcha yo'lni o'qimaydi — baytlardan dekodlaymiz.
        frame = cv2.imdecode(np.frombuffer(path.read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
        if frame is not None:
            frames.append((path.name, frame))
        if limit and len(frames) >= limit:
            break
    return frames


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Candidate model <-> ilova kodi mosligi")
    parser.add_argument("--weights", required=True)
    parser.add_argument("--images", default=str(PROJECT_ROOT / "ai_training" / "dataset" / "images" / "val"))
    parser.add_argument("--labels", default="", help="label papkasi (standart: images/<split> yonidan)")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)

    weights = Path(args.weights)
    if not weights.is_file():
        print(f"Weights topilmadi: {weights}")
        return 2
    images_dir = Path(args.images)
    labels_dir = Path(args.labels) if args.labels else images_dir.parent.parent / "labels" / images_dir.name
    frames = load_frames(images_dir, args.limit, labels_dir if labels_dir.is_dir() else None)
    if not frames:
        print(f"Kadr topilmadi: {args.images}")
        return 2

    from app.config import get_settings
    from app.database import session_scope
    from app.models.equipment import EquipmentCategory
    from app.services.yolo_service import YoloService, build_rules

    settings = get_settings()
    # Ilova qoidalarni DB dan yasaydi (`CameraMonitor.refresh_detection_rules`).
    # Alohida jarayonda bu qadam o'tkazib yuborilsa, `rules` bo'sh qoladi va
    # har bir deteksiya "noma'lum sinf" deb tashlanadi.
    with session_scope() as session:
        categories = session.scalars(
            select(EquipmentCategory).where(EquipmentCategory.is_active.is_(True))
        ).all()
        rules = build_rules(categories)

    service = YoloService(model_path=weights, rules=rules)
    checks: list[tuple[str, bool, str]] = []
    checks.append(("deteksiya qoidalari yuklandi", len(rules) >= 27, f"rules={len(rules)}"))

    loaded = service.ensure_loaded()
    checks.append(("model yuklandi", loaded, f"fallback={service._using_fallback}"))
    if not loaded:
        report(checks)
        return 1
    checks.append(("fallback ishlatilmayapti", not service._using_fallback, ""))

    names = getattr(service._model, "names", {}) or {}
    checks.append(("sinf soni 27", len(names) == 27, f"topildi={len(names)}"))
    checks.append(
        ("person sinfi 0", str(names.get(0, "")).lower() == "person", f"0={names.get(0)}")
    )

    total = 0
    persons = 0
    for name, frame in frames:
        detections = service.detect(frame, use_tracking=False)
        total += len(detections)
        persons += sum(1 for item in detections if getattr(item, "category", "") == "person"
                       or getattr(item, "class_name", "") == "person")
        categories = sorted({item.category for item in detections})
        print(f"  {name}: {len(detections)} obyekt {categories if categories else ''}")
    checks.append(("detect() obyekt qaytardi", total > 0, f"jami={total}"))
    checks.append(("person aniqlandi", persons > 0, f"person={persons}"))

    # ByteTrack: uchta ketma-ket kadr — tracker custom sinflar bilan ishlashi shart
    tracked_ok = True
    detail = ""
    try:
        for _, frame in frames[:3]:
            service.detect(frame, use_tracking=True)
    except Exception as exc:  # noqa: BLE001 - tracker xatosi har xil bo'ladi
        tracked_ok = False
        detail = f"{type(exc).__name__}: {exc}"
    checks.append(("ByteTrack (use_tracking=True)", tracked_ok, detail))

    report(checks)
    return 0 if all(passed for _, passed, _ in checks) else 1


def report(checks: list[tuple[str, bool, str]]) -> None:
    print("\n=== Natija ===")
    for label, passed, detail in checks:
        suffix = f"  ({detail})" if detail else ""
        print(f"  [{'PASS' if passed else 'FAIL'}] {label}{suffix}")
    ok = all(passed for _, passed, _ in checks)
    print(f"\nXulosa: {'mos keladi' if ok else 'MOS EMAS — modelni almashtirmang'}")


if __name__ == "__main__":
    sys.exit(main())
