"""Dataset kadrlarini avtomatik oldindan belgilash (pre-label).

Nima uchun kerak: qo'lda 100+ kadrga quti chizish juda uzoq. Joriy YOLO modeli
qutilarni o'zi topadi, bu skript ularni YOLO `.txt` formatiga yozadi va ustiga
rasmga chizib ko'rsatadi — odam faqat xatoni tuzatadi.

Ikki muhim yordamchi:

* **Papka bo'yicha sinf** — panelda yig'ilgan namuna nomi `mouse__mouse_001.jpg`
  ko'rinishida. Model mausni "cell phone" deb topgan bo'lsa, sinf avtomatik
  `mouse` ga to'g'rilanadi (chalkashuv jadvali orqali).
* **Preview** — `dataset/preview/<split>/` ichida qutilari chizilgan nusxa;
  LabelImg o'rnatmasdan ham Windows'da rasmni ochib tekshirish mumkin.

Misol:

    .venv\\Scripts\\python.exe scripts\\autolabel_dataset.py --dataset dataset
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}

# COCO nomlari toifa kodlaridan farq qiladigan joylar.
LABEL_ALIASES = {
    "cell_phone": "smartphone",
    "cellphone": "smartphone",
    "mobile_phone": "smartphone",
    "notebook": "laptop",
    "desktop_computer": "desktop_pc",
    "tvmonitor": "monitor",
    "display": "monitor",
    "monitor_screen": "monitor",
}

# Papka toifasi bilan model aytgan sinf chalkashsa — qaysi sinfni to'g'rilash.
CONFUSIONS = {
    "mouse": {"smartphone", "keyboard", "other_equipment"},
    "keyboard": {"mouse", "laptop"},
    "smartphone": {"mouse", "tablet"},
    "tablet": {"smartphone", "laptop"},
    "monitor": {"tv", "all_in_one_pc"},
    "all_in_one_pc": {"tv", "monitor", "laptop"},
    "tv": {"monitor", "all_in_one_pc"},
    "laptop": {"all_in_one_pc", "monitor", "keyboard"},
    "ip_phone": {"landline_phone", "smartphone"},
    "landline_phone": {"ip_phone", "smartphone"},
    "mini_camera": {"camera"},
    "camera": {"mini_camera"},
}

BOX = dict[str, Any]


def _utf8_console() -> None:
    """Chiqishni UTF-8 ga o‘tkazish (cp866 da `‘`, `—` yo‘q)."""
    if os.name == "nt":
        try:
            import ctypes

            ctypes.windll.kernel32.SetConsoleOutputCP(65001)  # pragma: no cover
        except Exception:  # pragma: no cover - konsol bo‘lmasa
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - yo‘naltirilgan oqim
            pass


def normalize(label: str) -> str:
    """`Cell phone` -> `cell_phone` (data.yaml nomlari bilan solishtirish uchun)."""
    return re.sub(r"[\s\-]+", "_", str(label).strip().lower())


def build_index(names: Mapping[int, str]) -> dict[str, int]:
    """data.yaml `names` dan `nom -> sinf raqami` jadvalini yasash."""
    return {normalize(name): int(class_id) for class_id, name in names.items()}


def resolve_label(label: str, index: Mapping[str, int]) -> int | None:
    """Model sinf nomini bizning sinf raqamiga o'girish (aliaslar bilan)."""
    key = normalize(label)
    if key in index:
        return index[key]
    alias = LABEL_ALIASES.get(key)
    if alias and alias in index:
        return index[alias]
    return None


def folder_hint(stem: str) -> str | None:
    """`mouse__mouse_001` -> `mouse`; panel namunalarida toifa nomi oldinda."""
    if "__" not in stem:
        return None
    prefix = normalize(stem.split("__", 1)[0])
    return prefix or None


def apply_folder_hint(boxes: Sequence[BOX], hint: str | None, index: Mapping[str, int]) -> list[BOX]:
    """Papka toifasi bilan chalkashadigan qutilarni to'g'ri sinfga o'girish.

    Masalan `mouse__...` kadrida model "cell phone" 0.81 topgan bo'lsa, quti
    masshtabi to'g'ri, faqat sinf xato — uni `mouse` ga o'giramiz.
    """
    if not hint or hint not in index:
        return list(boxes)
    allowed = CONFUSIONS.get(hint, set())
    if not allowed:
        return list(boxes)
    hint_id = index[hint]
    fixed: list[BOX] = []
    for box in boxes:
        if box["label"] in allowed:
            box = {**box, "label": hint, "class_id": hint_id, "promoted": True}
        fixed.append(box)
    return fixed


def yolo_lines(boxes: Iterable[BOX], width: int, height: int) -> list[str]:
    """Qutilarni YOLO formatiga: `sinf cx cy w h` (0..1 ga normallashtirilgan)."""
    if width <= 0 or height <= 0:
        return []
    lines: list[str] = []
    for box in boxes:
        x1, y1, x2, y2 = box["xyxy"]
        x1, x2 = sorted((max(0.0, min(float(x1), width)), max(0.0, min(float(x2), width))))
        y1, y2 = sorted((max(0.0, min(float(y1), height)), max(0.0, min(float(y2), height))))
        box_width = x2 - x1
        box_height = y2 - y1
        if box_width <= 1 or box_height <= 1:
            continue
        cx = (x1 + x2) / 2 / width
        cy = (y1 + y2) / 2 / height
        lines.append(
            f"{int(box['class_id'])} {cx:.6f} {cy:.6f} {box_width / width:.6f} {box_height / height:.6f}"
        )
    return lines


def image_files(folder: Path) -> list[Path]:
    if not folder.is_dir():
        return []
    return sorted(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def load_names(data_file: Path) -> dict[int, str]:
    import yaml

    payload = yaml.safe_load(data_file.read_text(encoding="utf-8")) or {}
    names = payload.get("names") or {}
    if isinstance(names, list):
        return {index: str(name) for index, name in enumerate(names)}
    return {int(class_id): str(name) for class_id, name in names.items()}


def default_model_path() -> str:
    from app.config import get_settings

    settings = get_settings()
    if settings.yolo_model_path.exists():
        return str(settings.yolo_model_path)
    return str(settings.yolo_fallback_model)


def draw_preview(source: Path, boxes: Sequence[BOX], destination: Path) -> bool:
    try:
        import cv2
    except ImportError:  # pragma: no cover - cv2 tests bilan birga o'rnatiladi
        return False
    frame = cv2.imread(str(source))
    if frame is None:
        return False
    for box in boxes:
        x1, y1, x2, y2 = (int(round(value)) for value in box["xyxy"])
        # To'g'rilangan quti — to'q sariq, model aytgani — yashil.
        color = (0, 140, 255) if box.get("promoted") else (0, 200, 0)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        caption = f"{box['label']} {box['confidence']:.2f}"
        if box.get("promoted"):
            caption += f" (edi: {box['original_label']})"
        cv2.putText(
            frame,
            caption,
            (x1, max(14, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            color,
            1,
            cv2.LINE_AA,
        )
    destination.parent.mkdir(parents=True, exist_ok=True)
    return bool(cv2.imwrite(str(destination), frame))


def main(argv: list[str] | None = None) -> int:
    _utf8_console()
    parser = argparse.ArgumentParser(description="Dataset kadrlarini oldindan belgilash")
    parser.add_argument("--dataset", default="dataset", help="dataset papkasi (standart: dataset)")
    parser.add_argument("--data", default="", help="data.yaml (standart: <dataset>/data.yaml)")
    parser.add_argument("--split", default="train", help="train | val")
    parser.add_argument("--model", default="", help="model fayli (standart: .env dagi YOLO modeli)")
    parser.add_argument("--conf", type=float, default=0.25, help="eng past ishonch")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--device", default="cpu", help="cpu | 0")
    parser.add_argument("--overwrite", action="store_true", help="mavjud label fayllarini qayta yozish")
    parser.add_argument("--no-hint", dest="hint", action="store_false", help="papka bo'yicha to'g'rilashni o'chirish")
    parser.add_argument("--no-preview", dest="preview", action="store_false", help="preview rasmlarini yozmaslik")
    parser.add_argument("--dry-run", action="store_true", help="fayl yozmasdan sinash")
    args = parser.parse_args(argv)

    dataset = Path(args.dataset).resolve()
    data_file = Path(args.data) if args.data else dataset / "data.yaml"
    if not data_file.is_file():
        print(f"data.yaml topilmadi: {data_file}")
        print("Avval: python scripts/train_yolo.py --init dataset")
        return 2
    try:
        names = load_names(data_file)
    except ImportError:
        print("PyYAML o'rnatilmagan. `pip install -r requirements-ai.txt` bajaring.")
        return 2

    images_folder = dataset / "images" / args.split
    labels_folder = dataset / "labels" / args.split
    preview_root = dataset / "preview" / args.split
    images = image_files(images_folder)
    if not images:
        print(f"Rasm topilmadi: {images_folder}")
        print("Avval: python scripts/train_yolo.py --import-samples dataset")
        return 2

    try:
        from ultralytics import YOLO
    except ImportError:
        print("Ultralytics o'rnatmagan. `pip install -r requirements-ai.txt` bajaring.")
        return 2

    # Prodakshn bilan bir xil geometriya qoidasi: kadrning yarmini egallagan
    # “maus” qutisi o'qitish to'plamiga tushmasin.
    from app.services.types import BBox
    from app.services.yolo_service import plausible_box

    model_path = args.model or default_model_path()
    print(f"Model      : {model_path}")
    print(f"Dataset    : {images_folder}")
    print(f"Sinflar    : {len(names)} ta")
    print(f"Rasmlar    : {len(images)} ta\n")

    index = build_index(names)
    # `index` — nomdan raqamga; qutiga esa kanonik nom kerak.
    canonical = {class_id: name for name, class_id in index.items()}
    model = YOLO(model_path)
    model_names = getattr(model, "names", {}) or {}

    labelled = skipped = promoted_total = empty = unknown_total = rejected = 0
    per_class: dict[str, int] = {}

    for image in images:
        label_path = labels_folder / f"{image.stem}.txt"
        if label_path.exists() and not args.overwrite:
            skipped += 1
            continue

        results = model.predict(
            source=str(image), conf=args.conf, imgsz=args.imgsz, device=args.device, verbose=False
        )
        height, width = 0, 0
        boxes: list[BOX] = []
        for result in results:
            shape = getattr(result, "orig_shape", None) or ()
            if len(shape) >= 2:
                height, width = int(shape[0]), int(shape[1])
            raw = getattr(result, "boxes", None)
            if raw is None or len(raw) == 0:
                continue
            coordinates = raw.xyxy.cpu().numpy()
            confidences = raw.conf.cpu().numpy()
            classes = raw.cls.cpu().numpy().astype(int)
            for position in range(len(classes)):
                label = str(model_names.get(int(classes[position]), classes[position]))
                class_id = resolve_label(label, index)
                if class_id is None:
                    unknown_total += 1
                    continue
                boxes.append(
                    {
                        "class_id": class_id,
                        "label": canonical[class_id],
                        "original_label": normalize(label),
                        "confidence": float(confidences[position]),
                        "xyxy": tuple(float(value) for value in coordinates[position][:4]),
                        "promoted": False,
                    }
                )

        if args.hint:
            hint = folder_hint(image.stem)
            boxes = apply_folder_hint(boxes, hint, index)
            promoted_total += sum(1 for box in boxes if box.get("promoted"))

        keep: list[BOX] = []
        for box in boxes:
            if plausible_box(box["label"], BBox(*box["xyxy"]), (height, width)):
                keep.append(box)
            else:
                rejected += 1
        boxes = keep

        lines = yolo_lines(boxes, width, height)
        if not lines:
            # Bo'sh `.txt` yozilmaydi: bunday kadr `--check` da “tasdiqsiz” bo'lib
            # qoladi va qo'lda belgilanishi kerakligi ko'rinib turadi.
            empty += 1
        if not args.dry_run:
            if lines:
                labels_folder.mkdir(parents=True, exist_ok=True)
                label_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            elif label_path.exists():
                # Bu yerga faqat `--overwrite` bilan yoki bo'sh fayl bo'lganda kelinadi:
                # eski (endi rad etilgan) qutilar qolib ketmasin.
                label_path.unlink()
            if args.preview:
                draw_preview(image, boxes, preview_root / f"{image.stem}.jpg")
        labelled += 1 if lines else 0
        for box in boxes:
            per_class[box["label"]] = per_class.get(box["label"], 0) + 1

    print(f"Belgilandi : {labelled} ta rasm ({promoted_total} ta quti papka bo'yicha to'g'rilandi)")
    print(f"Bo'sh      : {empty} ta rasm — model hech narsa topmadi (qo'lda belgilash kerak)")
    if skipped:
        print(f"O'tkazildi : {skipped} ta (label avvaldan bor; qayta yozish uchun --overwrite)")
    if unknown_total:
        print(f"Tanilmadi  : {unknown_total} ta quti (sinf data.yaml da yo'q, tashlab yuborildi)")
    if rejected:
        print(f"Geometriya : {rejected} ta quti tashlandi (shakl/o'lcham toifaga to'g'ri kelmadi)")
    if per_class:
        print("\nSinf bo'yicha qutilar:")
        for code, count in sorted(per_class.items(), key=lambda item: -item[1]):
            print(f"  {code:20s} {count}")
    if args.dry_run:
        print("\n--dry-run: hech qanday fayl yozilmadi.")
    else:
        print(f"\nLabel fayllari : {labels_folder}")
        if args.preview:
            print(f"Preview rasmlar : {preview_root}")
            print("Ularni Windows'da ochib tekshiring; xato qutilarni LabelImg/CVAT bilan tuzating.")
    print("\nKeyingi qadam: python scripts/train_yolo.py --check dataset/data.yaml")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
