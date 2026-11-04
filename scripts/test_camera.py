"""Kamera ulanishini tekshirish (CLI).

Bu skript modelni ishga tushirmaydi — faqat kamera manbasi ochilishini,
kadr o‘lchamini va FPS ni tekshiradi. Natija kadrlari `--out` papkasiga
yoziladi, shuning uchun virtual chiziq koordinatalarini shu kadr bo‘yicha
belgilash mumkin.

Misollar
--------
    python scripts/test_camera.py --code CAM-01
    python scripts/test_camera.py --source rtsp://user:pass@192.168.1.64:554/Streaming/Channels/101
    python scripts/test_camera.py --type usb --source 0 --seconds 5
    python scripts/test_camera.py --type file --source C:\\video\\kirish.mp4 --seconds 3
    python scripts/test_camera.py --type simulation --seconds 6
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import cv2  # noqa: E402

from app.config import get_settings  # noqa: E402
from app.database import init_db, session_scope  # noqa: E402
from app.models import Camera  # noqa: E402
from app.models.enums import SourceType  # noqa: E402
from app.services.camera_service import CameraConfig, CameraWorker  # noqa: E402
from app.utils.logging_config import setup_logging  # noqa: E402


def _config_from_db(code: str) -> CameraConfig:
    with session_scope() as session:
        camera = session.query(Camera).filter(Camera.code == code.strip().upper()).one_or_none()
        if camera is None:
            raise SystemExit(f"Kamera topilmadi: {code}")
        return CameraConfig.from_model(camera)


def _config_from_args(args: argparse.Namespace) -> CameraConfig:
    return CameraConfig(
        id=0,
        code="TEST",
        name="Kamera testi",
        location="",
        source_type=args.type,
        source=args.source or "",
        is_enabled=True,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kamera manbasini tekshirish")
    parser.add_argument("--code", help="bazadagi kamera kodi (masalan CAM-01)")
    parser.add_argument("--source", help="RTSP URL / qurilma raqami / fayl yo‘li")
    parser.add_argument(
        "--type",
        default=SourceType.RTSP.value,
        choices=[item.value for item in SourceType],
        help="manba turi",
    )
    parser.add_argument("--seconds", type=float, default=8.0, help="necha sekund kuzatish")
    parser.add_argument("--out", default="data/debug", help="kadrlar saqlanadigan papka")
    parser.add_argument("--save", type=int, default=3, help="saqlanadigan kadrlar soni")
    args = parser.parse_args(argv)

    if not args.code and args.type != SourceType.SIMULATION.value and not args.source:
        parser.error("--source yoki --code kiritilishi kerak")

    settings = get_settings()
    setup_logging(settings.log_level, settings.log_dir)
    init_db(settings)

    config = _config_from_db(args.code) if args.code else _config_from_args(args)

    print(f"Manba: {config.source_type} / {config.source or '—'}")
    print(f"Chiziq: {config.line if config.line else 'belgilanmagan'}")

    worker = CameraWorker(config, settings)
    worker.start()

    started = time.time()
    frames = 0
    last_report = started
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    saved = 0

    try:
        while time.time() - started < args.seconds:
            frame = worker.drain()
            if frame is not None:
                frames += 1
                if saved < args.save and frames in {1, 30, 90}:
                    target = out_dir / f"camera_{config.code}_{saved + 1:02d}.jpg"
                    cv2.imwrite(str(target), frame)
                    saved += 1
                    print(f"  kadr saqlandi: {target} ({frame.shape[1]}x{frame.shape[0]})")
            if time.time() - last_report >= 2.0:
                last_report = time.time()
                elapsed = time.time() - started
                print(
                    f"  holat={worker.status.value:<11} kadr={worker.frames_captured:<5} "
                    f"o‘rtacha={worker.frames_captured / elapsed:4.1f} fps"
                )
            time.sleep(0.02)
    finally:
        worker.stop()

    elapsed = max(0.001, time.time() - started)
    print()
    print("--- natija ---")
    print(f"Holat            : {worker.status.value} ({worker.status_message or 'xabar yo‘q'})")
    print(f"Olingan kadr     : {worker.frames_captured}")
    print(f"Tashlab yuborilgan: {worker.frames_dropped}")
    print(f"O‘rtacha FPS     : {worker.frames_captured / elapsed:.1f}")
    print(f"Oxirgi kadr      : {worker.last_frame_at}")

    if worker.frames_captured == 0:
        print()
        print("KADR OLINMADI. Tekshiring:")
        print("  * RTSP manzil, login/parol va tarmoq (ping 192.168.1.64)")
        print("  * Kamera H.264 substream berayotganini (masalan /Streaming/Channels/102)")
        print("  * USB kamera uchun --type usb --source 0 (yoki 1, 2 ...)")
        print("  * Windows Firewall / antivirus bloklanishi")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
