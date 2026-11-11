"""Candidate modelni production model bilan bir xil kadrlarda solishtirish.

Taqqoslash production sharoitida — `YOLO_DEVICE=cpu`, `imgsz` va `conf` `.env`
dagi qiymatlar bilan — bajariladi, chunki tizim CPU da ishlaydi. GPU raqamlari
faqat ma'lumot uchun chiqariladi.

O'lchanadi:
* CPU inference kechikishi (o'rtacha va p95) — production uchun muhim;
* aniqlangan obyektlar soni va sinf taqsimoti;
* `person` recall — `val` label'lari bo'yicha IoU >= 0.5 (tizimning tayanchi).

Misollar
--------
    python ai_training/scripts/benchmark.py --weights ai_training/models/<run>/best.pt
    python ai_training/scripts/benchmark.py --weights ... --baseline data/models/yolov8s.pt --limit 60
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "ai_training" / "dataset"
REPORTS = PROJECT_ROOT / "ai_training" / "reports"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
PERSON_NAMES = {"person", "people", "pedestrian"}


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_label_boxes(path: Path, class_id: int) -> list[tuple[float, float, float, float]]:
    boxes: list[tuple[float, float, float, float]] = []
    if not path.is_file():
        return boxes
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) != 5:
            continue
        try:
            if int(float(parts[0])) != class_id:
                continue
            cx, cy, width, height = (float(value) for value in parts[1:])
        except ValueError:
            continue
        boxes.append((cx - width / 2, cy - height / 2, cx + width / 2, cy + height / 2))
    return boxes


def iou(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def run_model(weights: Path, images: list[Path], labels_dir: Path | None, *, imgsz: int,
              conf: float, device: str) -> dict:
    from ultralytics import YOLO

    model = YOLO(str(weights))
    names = {int(key): str(value) for key, value in (model.names or {}).items()}
    person_id = next((key for key, value in names.items() if value.lower() in PERSON_NAMES), None)

    frames = []
    import cv2

    for image in images:
        frame = cv2.imread(str(image))
        if frame is not None:
            frames.append((image, frame))
    if not frames:
        raise SystemExit(f"Rasmlar o'qilmadi: {images[0].parent if images else '-'}")

    for _, frame in frames[:3]:  # isinish (birinchi chaqiriq sekin)
        model.predict(frame, imgsz=imgsz, conf=conf, device=device, verbose=False)

    latencies: list[float] = []
    detections = Counter()
    hits = total = 0
    for image, frame in frames:
        started = time.perf_counter()
        result = model.predict(frame, imgsz=imgsz, conf=conf, device=device, verbose=False)[0]
        latencies.append((time.perf_counter() - started) * 1000)
        predicted_person: list[tuple[float, ...]] = []
        height, width = frame.shape[:2]
        for box in result.boxes:
            class_id = int(box.cls.item())
            detections[names.get(class_id, str(class_id))] += 1
            if person_id is not None and class_id == person_id:
                x1, y1, x2, y2 = box.xyxy[0].tolist()
                predicted_person.append((x1 / width, y1 / height, x2 / width, y2 / height))
        if labels_dir is not None and person_id is not None:
            truth = load_label_boxes(labels_dir / f"{image.stem}.txt", person_id)
            if truth:
                total += len(truth)
                hits += sum(
                    1 for box in truth if any(iou(box, pred) >= 0.5 for pred in predicted_person)
                )

    params_m = sum(p.numel() for p in model.model.parameters()) / 1e6
    return {
        "weights": str(weights),
        "classes": len(names),
        "person_class": person_id,
        "params_m": round(params_m, 1),
        "images": len(frames),
        "latency_mean_ms": round(statistics.fmean(latencies), 1),
        "latency_p95_ms": round(sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)], 1),
        "detections": int(sum(detections.values())),
        "per_class": dict(detections.most_common(12)),
        "person_recall": round(hits / total, 3) if total else None,
        "person_gt": total,
    }


def main(argv: list[str] | None = None) -> int:
    env = read_env(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Candidate vs production model taqqoslash")
    parser.add_argument("--weights", required=True, help="candidate best.pt")
    parser.add_argument(
        "--baseline", default=str(PROJECT_ROOT / env.get("YOLO_FALLBACK_MODEL", "data/models/yolov8s.pt")),
        help="production model (standart: .env dagi fallback)",
    )
    parser.add_argument("--images", default=str(DATASET / "images" / "val"))
    parser.add_argument("--labels", default=str(DATASET / "labels" / "val"))
    parser.add_argument("--imgsz", type=int, default=int(env.get("YOLO_IMAGE_SIZE", 640)))
    parser.add_argument("--conf", type=float, default=float(env.get("YOLO_CONFIDENCE", 0.35)))
    parser.add_argument("--device", default=env.get("YOLO_DEVICE", "cpu"))
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", default="", help="markdown hisobot yo'li")
    args = parser.parse_args(argv)

    images_dir = Path(args.images)
    images = sorted(p for p in images_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES) \
        if images_dir.is_dir() else []
    if not images:
        print(f"Rasmlar topilmadi: {images_dir}")
        return 2
    if args.limit:
        images = images[: args.limit]

    labels_dir = Path(args.labels)
    labels_dir = labels_dir if labels_dir.is_dir() else None
    candidate = Path(args.weights)
    baseline = Path(args.baseline)
    if not candidate.is_file():
        print(f"Candidate topilmadi: {candidate}")
        return 2

    print(f"Kadrlar: {len(images)} | device={args.device} imgsz={args.imgsz} conf={args.conf}")
    print(f"Baseline: {baseline}")
    print(f"Candidate: {candidate}\n")

    results = {"candidate": run_model(candidate, images, labels_dir, imgsz=args.imgsz,
                                      conf=args.conf, device=args.device)}
    if baseline.is_file():
        results["baseline"] = run_model(baseline, images, labels_dir, imgsz=args.imgsz,
                                        conf=args.conf, device=args.device)
    else:
        print(f"Diqqat: baseline topilmadi ({baseline}) — faqat candidate o'lchandi.")

    lines = [
        "# Candidate vs baseline",
        "",
        f"Kadrlar: {len(images)} | device={args.device} | imgsz={args.imgsz} | conf={args.conf}",
        "",
        "| Ko'rsatkich | Baseline | Candidate |",
        "|---|---|---|",
    ]
    base = results.get("baseline", {})
    cand = results["candidate"]
    rows = [
        ("Model", "baseline", "candidate"),
        ("Sinf soni", base.get("classes"), cand.get("classes")),
        ("Parametrlar (M)", base.get("params_m"), cand.get("params_m")),
        ("CPU kechikish o'rtacha (ms)", base.get("latency_mean_ms"), cand.get("latency_mean_ms")),
        ("CPU kechikish p95 (ms)", base.get("latency_p95_ms"), cand.get("latency_p95_ms")),
        ("Aniqlangan obyekt", base.get("detections"), cand.get("detections")),
        ("person recall", base.get("person_recall"), cand.get("person_recall")),
    ]
    for label, left, right in rows[1:]:
        lines.append(f"| {label} | {left} | {right} |")

    ratio = None
    if base.get("latency_mean_ms"):
        ratio = round(cand["latency_mean_ms"] / base["latency_mean_ms"], 2)
        lines += ["", f"Kechikish nisbati (candidate/baseline): **{ratio}x**"]
    lines += ["", "Sinf taqsimoti (candidate):",
              "`" + json.dumps(cand.get("per_class", {}), ensure_ascii=False) + "`"]

    out = Path(args.out) if args.out else REPORTS / f"benchmark_{time.strftime('%Y%m%d_%H%M%S')}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out.with_suffix(".json")).write_text(json.dumps(results, indent=2), encoding="utf-8")

    print("\n".join(lines))
    print(f"\nHisobot: {out}")
    print(f"JSON    : {out.with_suffix('.json')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
