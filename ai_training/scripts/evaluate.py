"""Candidate modelni `val`/`test` bo'limida baholash (mAP, sinf bo'yicha).

`data.yaml` da `test:` bo'limi bo'lmasa, vaqtinchalik nusxa yaratiladi va
`images/test` mavjud bo'lsa qo'shiladi. Asl `data.yaml` o'zgartirilmaydi.

Misollar
--------
    python ai_training/scripts/evaluate.py --weights ai_training/models/<run>/best.pt
    python ai_training/scripts/evaluate.py --weights ... --split test --device cpu
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "ai_training" / "dataset"
REPORTS = PROJECT_ROOT / "ai_training" / "reports"
CONFIG = PROJECT_ROOT / "ai_training" / "configs" / "training.yaml"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def acceptance() -> dict:
    if not CONFIG.is_file():
        return {}
    payload = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    return payload.get("acceptance") or {}


def prepare_data(data_file: Path, split: str) -> Path:
    """`test` bo'limi kerak bo'lsa vaqtinchalik data.yaml yasash."""
    payload = yaml.safe_load(data_file.read_text(encoding="utf-8")) or {}
    if split != "test" or payload.get("test"):
        return data_file
    test_dir = DATASET / "images" / "test"
    images = [p for p in test_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES] \
        if test_dir.is_dir() else []
    if not images:
        raise SystemExit("test bo'limi bo'sh — `val` bilan baholang yoki kadrlarni ko'chiring.")
    payload["test"] = "images/test"
    temp = DATASET / "data.test.yaml"
    temp.write_text(yaml.safe_dump(payload, allow_unicode=True, sort_keys=False), encoding="utf-8")
    print(f"Vaqtinchalik data.yaml: {temp}")
    return temp


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Candidate modelni baholash")
    parser.add_argument("--weights", default="", help="best.pt (bo'sh = eng yangi candidate)")
    parser.add_argument("--data", default=str(DATASET / "data.yaml"))
    parser.add_argument("--split", default="val", choices=["val", "test", "train"])
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--device", default="cpu", help="cpu | 0 (production CPU da ishlaydi)")
    parser.add_argument("--out", default="")
    args = parser.parse_args(argv)

    weights = Path(args.weights) if args.weights else None
    if weights is None:
        found = sorted((PROJECT_ROOT / "ai_training" / "models").glob("*/best.pt"),
                       key=lambda path: path.stat().st_mtime)
        if not found:
            print("best.pt topilmadi — avval train.py ni ishga tushiring.")
            return 2
        weights = found[-1]
    if not weights.is_file():
        print(f"Weights topilmadi: {weights}")
        return 2

    data_file = prepare_data(Path(args.data), args.split)

    from ultralytics import YOLO

    print(f"Baholash: {weights}\n  data={data_file} split={args.split} device={args.device}")
    model = YOLO(str(weights))
    metrics = model.val(
        data=str(data_file), split=args.split, imgsz=args.imgsz, batch=args.batch,
        device=args.device, plots=True, verbose=False,
    )

    box = getattr(metrics, "box", None)
    summary = {
        "weights": str(weights),
        "split": args.split,
        "device": args.device,
        "imgsz": args.imgsz,
        "evaluated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "map50": round(float(getattr(box, "map50", 0.0)), 4) if box else None,
        "map50_95": round(float(getattr(box, "map", 0.0)), 4) if box else None,
        "precision": round(float(getattr(box, "mp", 0.0)), 4) if box else None,
        "recall": round(float(getattr(box, "mr", 0.0)), 4) if box else None,
        "per_class": {},
        "per_class_recall": {},
    }
    names = {int(key): str(value) for key, value in (getattr(metrics, "names", {}) or {}).items()}
    raw_maps = getattr(box, "maps", None)
    raw_present = getattr(box, "ap_class_index", None)
    present = [] if raw_present is None else [int(value) for value in raw_present]
    # `maps` — barcha sinflar bo'yicha massiv, lekin label'i yo'q sinflar
    # o'rtacha qiymat bilan to'ldiriladi. Haqiqiy sinflar — `ap_class_index`;
    # `r`/`p` esa xuddi shu tartibda (bo'sh sinflarsiz) keladi.
    if raw_maps is not None:
        values = [float(value) for value in raw_maps]
        wanted = present if present and max(present) < len(values) else list(range(len(values)))
        for class_index in wanted:
            name = names.get(class_index, str(class_index))
            summary["per_class"][name] = round(values[class_index], 4)
        raw_recall = getattr(box, "r", None)
        if raw_recall is not None and len(raw_recall) == len(wanted):
            for position, class_index in enumerate(wanted):
                name = names.get(class_index, str(class_index))
                summary["per_class_recall"][name] = round(float(raw_recall[position]), 4)

    limits = acceptance()
    min_map = float(limits.get("min_mAP50", 0.0))
    person_recall = summary["per_class_recall"].get("person")
    min_person = float(limits.get("min_person_recall", 0.0))
    ready = summary["map50"] is not None and summary["map50"] >= min_map
    if person_recall is not None and person_recall < min_person:
        ready = False

    lines = [
        "# Model baholash",
        "",
        f"* weights: `{weights}`",
        f"* split: `{args.split}` | device: `{args.device}` | imgsz: {args.imgsz}",
        f"* mAP50: **{summary['map50']}** | mAP50-95: {summary['map50_95']}",
        f"* precision: {summary['precision']} | recall: {summary['recall']}",
        "",
        "## Sinf bo'yicha mAP50-95",
        "",
        "| Sinf | mAP50-95 | Recall |",
        "|---|---|---|",
    ]
    for name, value in sorted(summary["per_class"].items(), key=lambda item: -item[1]):
        recall = summary["per_class_recall"].get(name, "-")
        lines.append(f"| {name} | {value} | {recall} |")
    map_verdict = "o'tdi" if summary["map50"] is not None and summary["map50"] >= min_map else "O'TMADI"
    person_verdict = "-" if person_recall is None else (
        "o'tdi" if person_recall >= min_person else "O'TMADI"
    )
    verdict = (
        "productionga nomzod sifatida saqlash mumkin"
        if ready
        else "productionga TAYYOR EMAS"
    )
    lines += [
        "",
        "## Qabul chegaralari",
        "",
        f"* mAP50 >= {min_map}: {map_verdict}",
        f"* person recall >= {min_person}: {person_verdict} ({person_recall})",
        f"* Xulosa: **{verdict}**",
    ]

    out = Path(args.out) if args.out else REPORTS / f"eval_{args.split}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    summary["accepted"] = bool(ready)
    out.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    print(f"\nJSON: {out}")
    print(f"MD  : {out.with_suffix('.md')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
