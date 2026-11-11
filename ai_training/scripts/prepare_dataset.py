"""Yig'ilgan xom kadrlardan YOLO datasetini tayyorlash.

Vazifasi:
1. `ai_training/dataset/raw/<sessiya>/` dagi kadrlarni tekshirib, `images/train` ga ko'chirish.
2. Bir xil va deyarli bir xil kadrlarni tashlab yuborish (train/val orasida takrorlanmasin).
3. `train`/`val` bo'linishini loyihaning o'z skripti orqali bajarish
   (`scripts/train_yolo.py --split-val`), ya'ni mantiq ikki joyda yozilmaydi.
4. Dataset manifestini yozish (`manifests/<vaqt>.json`) — versiya nazorati uchun.

Misollar
--------
    python ai_training/scripts/prepare_dataset.py --session session_20261105_140000
    python ai_training/scripts/prepare_dataset.py --all --ratio 0.2 --force
    python ai_training/scripts/prepare_dataset.py --all --dry-run
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DATASET = PROJECT_ROOT / "ai_training" / "dataset"
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
NEAR_DUPLICATE_DISTANCE = 3  # dHash bo'yicha 64 bitdan nechta bit farq "bir xil" hisoblanadi


def dhash(path: Path, size: int = 8) -> int:
    """DHash: kichik o'zgarishlarga chidamli, deyarli bir xil kadrlarni topadi."""
    image = Image.open(path).convert("L").resize((size + 1, size), Image.BILINEAR)
    pixels = list(image.getdata())
    bits = 0
    for row in range(size):
        for col in range(size):
            left = pixels[row * (size + 1) + col]
            right = pixels[row * (size + 1) + col + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(left: int, right: int) -> int:
    return bin(left ^ right).count("1")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def existing_images() -> list[Path]:
    found: list[Path] = []
    for split in ("train", "val", "test"):
        folder = DATASET / "images" / split
        if folder.is_dir():
            found.extend(p for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    return found


def run_project_script(*args: str) -> int:
    """Loyihaning mavjud `scripts/train_yolo.py` skriptini chaqirish."""
    command = [sys.executable, str(PROJECT_ROOT / "scripts" / "train_yolo.py"), *args]
    print("\n$ " + " ".join(command[1:]))
    return subprocess.call(command, cwd=str(PROJECT_ROOT))


def build_manifest(extra: dict) -> dict:
    """Joriy holatni qayd etish: kadr/label soni va har faylning sha256'si."""
    manifest = {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "counts": {}, "labels": {}, "images": {},
    }
    manifest.update(extra)
    for split in ("train", "val", "test"):
        images = sorted(p for p in (DATASET / "images" / split).glob("*")
                        if p.suffix.lower() in IMAGE_SUFFIXES)
        labels = sorted((DATASET / "labels" / split).glob("*.txt"))
        manifest["counts"][split] = len(images)
        manifest["labels"][split] = len(labels)
        for path in images:
            manifest["images"][path.relative_to(DATASET).as_posix()] = sha256(path)
        for path in labels:
            manifest["images"][path.relative_to(DATASET).as_posix()] = sha256(path)
    return manifest


def write_manifest(manifest: dict) -> Path:
    manifests = DATASET / "manifests"
    manifests.mkdir(parents=True, exist_ok=True)
    out = manifests / f"dataset_{time.strftime('%Y%m%d_%H%M%S')}.json"
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Dataset tayyorlash (raw -> images/train)")
    parser.add_argument("--raw", default=str(DATASET / "raw"), help="xom kadrlar papkasi")
    parser.add_argument("--session", default="", help="bitta sessiya nomi")
    parser.add_argument("--all", action="store_true", help="barcha sessiyalarni olish")
    parser.add_argument("--ratio", type=float, default=0.2, help="val ulushi")
    parser.add_argument("--force", action="store_true", help="val bo'lgan bo'lsa ham qayta bo'lish")
    parser.add_argument("--limit", type=int, default=0, help="ko'pi bilan shuncha kadr")
    parser.add_argument("--dry-run", action="store_true", help="faqat hisobot, fayl yozilmaydi")
    parser.add_argument("--skip-split", action="store_true", help="train/val bo'linmasin")
    parser.add_argument("--manifest-only", action="store_true",
                        help="nusxalamasdan faqat manifest yozish (versiya qayd etish)")
    args = parser.parse_args(argv)

    if args.manifest_only:
        out = write_manifest(build_manifest({"mode": "manifest-only"}))
        print(f"Manifest: {out}")
        return 0

    raw_root = Path(args.raw)
    if args.session:
        sessions = [raw_root / args.session]
    elif args.all:
        sessions = sorted(p for p in raw_root.iterdir() if p.is_dir()) if raw_root.is_dir() else []
    else:
        print("Sessiya tanlanmadi: --session <nom> yoki --all")
        return 2
    sessions = [p for p in sessions if p.is_dir()]
    if not sessions:
        print(f"Xom kadrlar topilmadi: {raw_root}")
        print("Avval: python ai_training/scripts/collect_frames.py --camera 101 --count 120")
        return 2

    target = DATASET / "images" / "train"
    if not args.dry_run:
        target.mkdir(parents=True, exist_ok=True)

    keep: list[tuple[Path, int]] = []
    for image in sorted(existing_images()):
        keep.append((image, dhash(image)))

    seen_digests = {sha256(path) for path in existing_images()}
    copied = skipped_duplicate = skipped_near = 0
    per_session: dict[str, int] = {}

    for session in sessions:
        frames = sorted(p for p in session.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
        session_copied = 0
        for frame in frames:
            if args.limit and copied >= args.limit:
                break
            digest = sha256(frame)
            if digest in seen_digests:
                skipped_duplicate += 1
                continue
            fingerprint = dhash(frame)
            if any(hamming(fingerprint, other) < NEAR_DUPLICATE_DISTANCE for _, other in keep):
                skipped_near += 1
                continue
            seen_digests.add(digest)
            keep.append((frame, fingerprint))
            copied += 1
            session_copied += 1
            if not args.dry_run:
                destination = target / f"{session.name}__{frame.name}"
                if destination.exists():
                    continue
                shutil.copy2(frame, destination)
        per_session[session.name] = session_copied

    print(f"\nNusxalandi  : {copied} kadr  -> {target}")
    print(f"Takror (aynan): {skipped_duplicate}")
    print(f"Takror (yaqin): {skipped_near}")
    for name, count in per_session.items():
        print(f"  {name:28s} {count}")

    if args.dry_run:
        print("\n--dry-run: hech narsa yozilmadi.")
        return 0

    if not args.skip_split:
        code = run_project_script(
            "--split-val", str(args.ratio), *(["--force"] if args.force else []),
            "--data", str(DATASET / "data.yaml"),
        )
        if code != 0:
            print("\nBo'linish bajarilmadi (yuqoridagi xabarni o'qing).")

    manifest = build_manifest({
        "sessions": per_session,
        "copied": copied,
        "skipped_exact_duplicates": skipped_duplicate,
        "skipped_near_duplicates": skipped_near,
        "near_duplicate_threshold": NEAR_DUPLICATE_DISTANCE,
    })
    out = write_manifest(manifest)
    print(f"\nManifest: {out}")
    print("Keyingi qadam: label'lar (scripts/autolabel_dataset.py) va "
          "ai_training/scripts/validate_labels.py")
    return 0


if __name__ == "__main__":
    sys.exit(main())
