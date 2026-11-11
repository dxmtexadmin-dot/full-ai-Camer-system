"""YOLO candidate modelini o'qitish (ai_training, alohida jarayon).

Asosiy ilova bilan aloqasi yo'q: hech qanday faylni `data/models/` ga
ko'chirmaydi, `.env` ni o'zgartirmaydi va ishlab turgan pipeline'ga tegmaydi.
`best.pt`/`last.pt` faqat `ai_training/runs/<run>/weights/` va
`ai_training/models/<run>/` da qoladi. Productionga chiqarish — `export.py` ishi.

Misollar
--------
    python ai_training/scripts/train.py --dry-run
    python ai_training/scripts/train.py
    python ai_training/scripts/train.py --epochs 40 --batch 8
    python ai_training/scripts/train.py --resume ai_training/runs/candidate_20261105_1500
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG = PROJECT_ROOT / "ai_training" / "configs" / "training.yaml"
MODELS = PROJECT_ROOT / "ai_training" / "models"
REPORTS = PROJECT_ROOT / "ai_training" / "reports"


def load_config(path: Path) -> dict:
    if not path.is_file():
        raise SystemExit(f"Sozlama topilmadi: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def ensure_weights(name: str) -> Path:
    """Pretrained checkpoint'ni `ai_training/models/` ichiga yuklash."""
    MODELS.mkdir(parents=True, exist_ok=True)
    target = MODELS / name
    if target.is_file():
        print(f"Pretrained checkpoint: {target}")
        return target
    try:
        from ultralytics.utils.downloads import attempt_download_asset

        resolved = Path(attempt_download_asset(name))
    except Exception as exc:  # noqa: BLE001 - tarmoq/xabar mazmuni har xil
        raise SystemExit(f"Pretrained checkpoint yuklanmadi ({name}): {exc}") from exc
    if resolved.resolve() != target.resolve() and resolved.is_file():
        shutil.move(str(resolved), target)
    if not target.is_file():
        raise SystemExit(f"Checkpoint kutilgan joyda emas: {target}")
    print(f"Pretrained checkpoint yuklandi: {target} ({target.stat().st_size / 1e6:.1f} MB)")
    return target


def check_cuda(device: str) -> str:
    """CUDA mavjudligini tekshirish. GPU yo'q bo'lsa xato yashirilmaydi."""
    try:
        import torch
    except ImportError as exc:
        raise SystemExit("PyTorch o'rnatilmagan. ai_training muhitini tayyorlang.") from exc
    if device == "cpu":
        print("Diqqat: device=cpu — o'qitish juda sekin bo'ladi (soatlar).")
        return "cpu"
    if not torch.cuda.is_available():
        raise SystemExit(
            "CUDA topilmadi: torch CPU build o'rnatilgan.\n"
            "Yechim: ai_training muhitini CUDA bilan qayta quring:\n"
            "  ai_training\\.venv\\Scripts\\python -m pip install torch torchvision "
            "--index-url https://download.pytorch.org/whl/cu128"
        )
    print(
        f"GPU: {torch.cuda.get_device_name(0)} | torch {torch.__version__} | "
        f"CUDA {torch.version.cuda}"
    )
    return device


def main(argv: list[str] | None = None) -> int:
    config = load_config(CONFIG)
    parser = argparse.ArgumentParser(description="YOLO candidate o'qitish")
    parser.add_argument("--config", default=str(CONFIG))
    parser.add_argument("--model", default=config.get("model", "yolo26m.pt"))
    parser.add_argument("--data", default=config.get("data", "ai_training/dataset/data.yaml"))
    parser.add_argument("--epochs", type=int, default=int(config.get("epochs", 100)))
    parser.add_argument("--imgsz", type=int, default=int(config.get("imgsz", 640)))
    parser.add_argument("--batch", type=int, default=int(config.get("batch", 16)))
    parser.add_argument("--patience", type=int, default=int(config.get("patience", 25)))
    parser.add_argument("--workers", type=int, default=int(config.get("workers", 8)))
    parser.add_argument("--device", default=str(config.get("device", "0")))
    parser.add_argument("--project", default=str(config.get("project", "ai_training/runs")))
    parser.add_argument("--name", default=str(config.get("name", "candidate")))
    parser.add_argument("--resume", default="", help="to'xtagan run papkasi (last.pt)")
    parser.add_argument("--dry-run", action="store_true", help="faqat buyruqni ko'rsatish")
    args = parser.parse_args(argv)

    data_file = (PROJECT_ROOT / args.data).resolve()
    if not data_file.is_file():
        print(f"data.yaml topilmadi: {data_file}")
        print("Avval: python scripts/train_yolo.py --init ai_training/dataset")
        return 2
    if not (PROJECT_ROOT / "ai_training" / "dataset" / "images" / "train").is_dir():
        print("Dataset bo'sh: avval kadr yig'ing (collect_frames.py).")
        return 2

    if args.resume:
        run_dir = (PROJECT_ROOT / args.resume).resolve()
        last = run_dir / "weights" / "last.pt"
        if not last.is_file():
            print(f"last.pt topilmadi: {last}")
            return 2
        check_cuda(args.device) if args.device != "cpu" else None
        print(f"Davom ettirish: {last}")
        if args.dry_run:
            print(f"  YOLO('{last}').train(resume=True)")
            return 0
        from ultralytics import YOLO

        YOLO(str(last)).train(resume=True)
        return 0

    weights = ensure_weights(args.model)
    device = check_cuda(args.device)

    run_name = f"{args.name}_{time.strftime('%Y%m%d_%H%M%S')}"
    overrides = {
        "data": str(data_file),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "patience": args.patience,
        "workers": args.workers,
        "device": device,
        "project": str((PROJECT_ROOT / args.project).resolve()),
        "name": run_name,
        "seed": int(config.get("seed", 0)),
        "amp": bool(config.get("amp", True)),
        "cos_lr": bool(config.get("cos_lr", True)),
        "optimizer": str(config.get("optimizer", "auto")),
        "cache": bool(config.get("cache", False)),
        "pretrained": True,
        "plots": True,
        "exist_ok": False,
    }
    print("\nO'qitish parametrlari:")
    for key, value in overrides.items():
        print(f"  {key:<10} {value}")

    if args.dry_run:
        print("\n--dry-run: o'qitish boshlanmadi.")
        return 0

    REPORTS.mkdir(parents=True, exist_ok=True)
    meta_path = REPORTS / f"train_{run_name}.json"
    meta_path.write_text(
        json.dumps({"started_at": time.strftime("%Y-%m-%dT%H:%M:%S"), "overrides": overrides,
                    "weights": str(weights)}, indent=2),
        encoding="utf-8",
    )

    from ultralytics import YOLO

    started = time.time()
    model = YOLO(str(weights))
    model.train(**overrides)
    elapsed = time.time() - started

    run_dir = Path(overrides["project"]) / run_name
    best = run_dir / "weights" / "best.pt"
    last = run_dir / "weights" / "last.pt"
    print(f"\nO'qitish tugadi: {elapsed / 60:.1f} daqiqa")
    print(f"Run papkasi: {run_dir}")
    print(f"best.pt: {best if best.is_file() else 'TOPILMADI'}")
    print(f"last.pt: {last if last.is_file() else 'TOPILMADI'}")

    archive = MODELS / run_name
    if best.is_file():
        archive.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, archive / "best.pt")
        if last.is_file():
            shutil.copy2(last, archive / "last.pt")
        print(f"Arxiv: {archive}")
        print("\nProduction modeli O'ZGARMADI. Keyingi qadam:")
        print(f"  python ai_training/scripts/evaluate.py --weights {archive / 'best.pt'}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta.update(
        {
            "finished_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "minutes": round(elapsed / 60, 1),
            "run_dir": str(run_dir),
            "best": str(best) if best.is_file() else "",
        }
    )
    meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return 0 if best.is_file() else 1


if __name__ == "__main__":
    sys.exit(main())
