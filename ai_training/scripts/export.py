"""O'qitilgan modelni **candidate** sifatida saqlash va rollback.

Qoidalar:
* production model (`data/models/equipment_best.pt`) va fallback
  (`data/models/yolov8s.pt`) hech qachon ustiga yozilmaydi;
* yangi model faqat `data/models/equipment_candidate.pt` ga tushadi;
* `.env` faqat `--switch` berilganda o'zgaradi va avval `.env.bak-<vaqt>` nusxasi olinadi;
* `--rollback` oxirgi nusxadan `.env` ni tiklaydi.

Misollar
--------
    python ai_training/scripts/export.py --weights ai_training/models/<run>/best.pt
    python ai_training/scripts/export.py --weights ... --switch
    python ai_training/scripts/export.py --rollback
    python ai_training/scripts/export.py --rollback-list
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = PROJECT_ROOT / ".env"
CANDIDATE = PROJECT_ROOT / "data" / "models" / "equipment_candidate.pt"
PROTECTED = [
    PROJECT_ROOT / "data" / "models" / "equipment_best.pt",
    PROJECT_ROOT / "data" / "models" / "yolov8s.pt",
]
KEY = "YOLO_MODEL_PATH"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_env_lines() -> list[str]:
    if not ENV_FILE.is_file():
        raise SystemExit(f".env topilmadi: {ENV_FILE}")
    return ENV_FILE.read_text(encoding="utf-8").splitlines()


def env_value(lines: list[str], key: str) -> str:
    for line in lines:
        stripped = line.strip()
        if stripped.startswith(f"{key}="):
            return stripped.split("=", 1)[1].strip()
    return ""


def backup_env() -> Path:
    target = ENV_FILE.with_name(f".env.bak-{time.strftime('%Y%m%d_%H%M%S')}")
    shutil.copy2(ENV_FILE, target)
    print(f".env nusxasi: {target}")
    return target


def write_env_value(key: str, value: str) -> None:
    lines = read_env_lines()
    replaced = False
    for index, line in enumerate(lines):
        if line.strip().startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            replaced = True
            break
    if not replaced:
        lines.append(f"{key}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")


def latest_backup() -> Path | None:
    backups = sorted(ENV_FILE.parent.glob(".env.bak-*"))
    return backups[-1] if backups else None


def do_rollback() -> int:
    backup = latest_backup()
    if backup is None:
        print("Zaxira nusxa yo'q — rollback qilish uchun .env.bak-* fayl topilmadi.")
        return 1
    shutil.copy2(backup, ENV_FILE)
    print(f".env tiklandi: {backup.name}")
    print(f"Joriy {KEY}={env_value(read_env_lines(), KEY)}")
    print("Serverni qayta ishga tushiring: .venv\\Scripts\\python run.py")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Candidate modelni eksport qilish / rollback")
    parser.add_argument("--weights", default="", help="best.pt yo'li (bo'sh = eng yangi)")
    parser.add_argument("--target", default=str(CANDIDATE))
    parser.add_argument("--force", action="store_true", help="mavjud candidate ustiga yozish")
    parser.add_argument("--switch", action="store_true", help=".env dagi model yo'lini candidate ga o'tkazish")
    parser.add_argument("--rollback", action="store_true", help="oxirgi .env nusxasini tiklash")
    parser.add_argument("--rollback-list", action="store_true", help="mavjud nusxalarni ko'rsatish")
    parser.add_argument("--metrics", default="", help="evaluate/benchmark JSON yo'li (metadata uchun)")
    args = parser.parse_args(argv)

    if args.rollback_list:
        for path in sorted(ENV_FILE.parent.glob(".env.bak-*")):
            print(path.name)
        print(f"Joriy {KEY}={env_value(read_env_lines(), KEY)}")
        return 0
    if args.rollback:
        return do_rollback()

    weights = Path(args.weights) if args.weights else None
    if weights is None:
        candidates = sorted(
            (PROJECT_ROOT / "ai_training" / "models").glob("*/best.pt"),
            key=lambda path: path.stat().st_mtime,
        )
        if not candidates:
            print("best.pt topilmadi — avval train.py ni ishga tushiring.")
            return 2
        weights = candidates[-1]
    if not weights.is_file():
        print(f"Weights topilmadi: {weights}")
        return 2

    target = Path(args.target)
    resolved_target = target.resolve()
    for protected in PROTECTED:
        if resolved_target == protected.resolve():
            print(f"TAQIQLANGAN: {protected.name} — production/fallback model ustiga yozilmaydi.")
            return 2
    if target.exists() and not args.force:
        print(f"Candidate allaqachon bor: {target}")
        print("Ustiga yozish uchun --force qo'shing (eski candidate yo'qoladi).")
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(weights, target)
    info = {
        "source": str(weights),
        "size_bytes": target.stat().st_size,
        "sha256": sha256(target),
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "production_model": str(PROJECT_ROOT / "data" / "models" / "equipment_best.pt"),
        "activate": f"set {KEY}=data/models/equipment_candidate.pt in .env, then restart",
        "rollback": f"python ai_training/scripts/export.py --rollback",
    }
    if args.metrics:
        metrics_path = Path(args.metrics)
        if metrics_path.is_file():
            info["metrics"] = json.loads(metrics_path.read_text(encoding="utf-8"))
    target.with_suffix(".json").write_text(json.dumps(info, indent=2), encoding="utf-8")
    print(f"Candidate saqlandi: {target} ({target.stat().st_size / 1e6:.1f} MB)")
    print(f"Metadata          : {target.with_suffix('.json')}")
    print(f"sha256            : {info['sha256'][:16]}...")
    print("\nProduction model o'zgarmadi "
          f"(YOLO_MODEL_PATH={env_value(read_env_lines(), KEY)}).")

    if args.switch:
        backup_env()
        write_env_value(KEY, "data/models/equipment_candidate.pt")
        print(f"{KEY}=data/models/equipment_candidate.pt yozildi")
        print("Endi serverni qayta ishga tushiring: .venv\\Scripts\\python run.py")
        print("Qaytarish: python ai_training/scripts/export.py --rollback")
    else:
        print("\nYoqish uchun (qo'lda): --switch yoki Sozlamalar sahifasida model yo'lini o'zgartirish.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
