"""YOLO label'larini tekshirish va inson ko'rigi uchun ro'yxat tayyorlash.

Mashina yozgan label'lar (avtomatik annotatsiya) 100% to'g'ri deb qabul
qilinmaydi. Skript uch turdagi muammoni ajratadi:

* **xato**  — fayl formati buzilgan, koordinata 0..1 dan tashqarida, sinf raqami
  yo'q, rasm uchun label umuman yo'q. Bunday dataset bilan o'qitish to'xtatiladi.
* **shubhali** — juda kichik/qirqilgan quti, rasm chetidan chiqib ketgan, bitta
  rasmda 30 dan ortiq quti, bo'sh label (`0 0 0 0 0`).
* **ma'lumot** — sinf bo'yicha statistika va `person` sinfining holati.

Natija: `ai_training/reports/label_review.csv` — har bir quti uchun alohida
qator. Inson shu jadvalni ko'rib, kerakli `.txt` fayllarni tuzatadi.

Misollar
--------
    python ai_training/scripts/validate_labels.py
    python ai_training/scripts/validate_labels.py --split val --strict
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import sys
from collections import Counter
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "ai_training" / "dataset"
REPORTS = PROJECT_ROOT / "ai_training" / "reports"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MIN_BOX_SIDE = 0.004  # ~3 px (640px kadrda) — undan kichik quti o'qitishga zarar
MAX_BOXES = 30


def load_names(data_file: Path) -> dict[int, str]:
    payload = yaml.safe_load(data_file.read_text(encoding="utf-8")) or {}
    names = payload.get("names") or {}
    if isinstance(names, list):
        return {index: str(name) for index, name in enumerate(names)}
    return {int(key): str(value) for key, value in names.items()}


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="YOLO label tekshiruvi")
    parser.add_argument("--dataset", default=str(DATASET))
    parser.add_argument("--data", default="", help="data.yaml (standart: <dataset>/data.yaml)")
    parser.add_argument("--split", default="", help="train | val | test (bo'sh = hammasi)")
    parser.add_argument("--strict", action="store_true", help="shubhali qatorlar ham xato bo'lsin")
    parser.add_argument("--csv", default=str(REPORTS / "label_review.csv"))
    args = parser.parse_args(argv)

    dataset = Path(args.dataset)
    data_file = Path(args.data) if args.data else dataset / "data.yaml"
    if not data_file.is_file():
        print(f"data.yaml topilmadi: {data_file}")
        return 2
    names = load_names(data_file)

    splits = [args.split] if args.split else ["train", "val", "test"]
    errors: list[str] = []
    warnings: list[str] = []
    rows: list[dict[str, object]] = []
    class_counter: Counter[int] = Counter()
    split_counter: dict[str, int] = {}
    digests: dict[str, str] = {}
    duplicates: list[str] = []

    for split in splits:
        images_dir = dataset / "images" / split
        labels_dir = dataset / "labels" / split
        if not images_dir.is_dir():
            continue
        images = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        split_counter[split] = len(images)
        for image in images:
            fingerprint = digest(image)
            if fingerprint in digests:
                duplicates.append(f"{image.name} == {digests[fingerprint]} ({split})")
                errors.append(f"{split}/{image.name}: aynan bir xil rasm ikki marta ({digests[fingerprint]})")
            else:
                digests[fingerprint] = f"{split}/{image.name}"

            label = labels_dir / f"{image.stem}.txt"
            if not label.is_file():
                errors.append(f"{split}/{image.name}: label fayli yo'q")
                continue
            lines = [line.strip() for line in label.read_text(encoding="utf-8").splitlines()]
            lines = [line for line in lines if line]
            if not lines:
                warnings.append(f"{split}/{image.name}: label bo'sh (obyekt yo'q?)")
                rows.append(
                    {
                        "split": split,
                        "image": image.name,
                        "line": 0,
                        "class_id": "",
                        "class_name": "",
                        "cx": "",
                        "cy": "",
                        "w": "",
                        "h": "",
                        "status": "empty",
                        "note": "label fayli bo'sh",
                    }
                )
                continue
            if len(lines) > MAX_BOXES:
                warnings.append(f"{split}/{image.name}: {len(lines)} ta quti (juda ko'p)")

            for number, line in enumerate(lines, start=1):
                parts = line.split()
                if len(parts) != 5:
                    errors.append(f"{split}/{image.name}#{number}: 5 ta qiymat kerak, {len(parts)} ta bor")
                    continue
                try:
                    class_id = int(float(parts[0]))
                    cx, cy, width, height = (float(value) for value in parts[1:])
                except ValueError:
                    errors.append(f"{split}/{image.name}#{number}: sonlar o'qilmadi — «{line}»")
                    continue
                if class_id not in names:
                    errors.append(f"{split}/{image.name}#{number}: sinf {class_id} data.yaml da yo'q")
                    continue
                status, note = "ok", ""
                if not all(0.0 <= value <= 1.0 for value in (cx, cy, width, height)):
                    status, note = "error", "koordinata 0..1 dan tashqarida"
                    errors.append(f"{split}/{image.name}#{number}: {note}")
                elif width <= 0 or height <= 0:
                    status, note = "error", "kenglik/balandlik nol"
                    errors.append(f"{split}/{image.name}#{number}: {note}")
                elif width < MIN_BOX_SIDE or height < MIN_BOX_SIDE:
                    status, note = "suspicious", "juda kichik quti"
                    warnings.append(f"{split}/{image.name}#{number}: {note}")
                elif cx - width / 2 < -0.01 or cx + width / 2 > 1.01 or cy - height / 2 < -0.01 or cy + height / 2 > 1.01:
                    status, note = "suspicious", "quti rasm chetidan chiqadi"
                    warnings.append(f"{split}/{image.name}#{number}: {note}")
                elif width > 0.98 and height > 0.98:
                    status, note = "suspicious", "quti butun kadrni qoplaydi"
                    warnings.append(f"{split}/{image.name}#{number}: {note}")
                class_counter[class_id] += 1
                rows.append(
                    {
                        "split": split,
                        "image": image.name,
                        "line": number,
                        "class_id": class_id,
                        "class_name": names.get(class_id, "?"),
                        "cx": f"{cx:.5f}",
                        "cy": f"{cy:.5f}",
                        "w": f"{width:.5f}",
                        "h": f"{height:.5f}",
                        "status": status,
                        "note": note,
                    }
                )

    out = Path(args.csv)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["split", "image", "line", "class_id", "class_name", "cx", "cy", "w", "h",
                        "status", "note"],
        )
        writer.writeheader()
        writer.writerows(rows)

    print("Splitlar   :", ", ".join(f"{k}={v}" for k, v in split_counter.items()) or "bo'sh")
    print(f"Qutilar    : {len(rows)}")
    print(f"Xatolar    : {len(errors)}")
    print(f"Shubhali   : {len(warnings)}")
    print(f"Takrorlar  : {len(duplicates)}")
    print(f"Ko'rik CSV : {out}")
    print("\nSinf bo'yicha qutilar:")
    for class_id in sorted(names):
        count = class_counter.get(class_id, 0)
        marker = "" if count else "   <- yo'q"
        print(f"  {names[class_id]:<18}{count:5d}{marker}")
    if 0 not in class_counter:
        print("\nDIQQAT: `person` sinfi uchun bitta ham quti yo'q — bunday model xodimni ko'rmaydi.")

    for line in errors[:20]:
        print(f"  XATO: {line}")
    for line in warnings[:15]:
        print(f"  SHUBHALI: {line}")
    if len(errors) > 20:
        print(f"  ... yana {len(errors) - 20} ta xato")

    if errors:
        return 1
    if args.strict and warnings:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
