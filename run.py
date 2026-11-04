"""Ishga tushirish nuqtasi.

    python run.py                # .env dagi APP_HOST/APP_PORT
    python run.py --reload       # ishlab chiqish rejimi (kod o‘zgarsa qayta yuklanadi)
    python run.py --port 9000

`run.bat` (Windows) va `run.sh` (Linux/macOS) shu faylni chaqiradi.
"""

from __future__ import annotations

import argparse

import uvicorn

from app.config import get_settings


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="AI CCTV inventar tizimi")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--reload", action="store_true", help="kod o‘zgarganda qayta yuklash")
    parser.add_argument("--log-level", default=settings.log_level.lower())
    args = parser.parse_args()

    print(f"AI CCTV inventar tizimi -> http://{args.host}:{args.port}")
    print(f"Baza: {settings.database_url}")
    if settings.simulation_mode:
        print("SIMULYATSIYA REJIMI yoqilgan: kamera o‘rniga sintetik kadr ishlatiladi.")

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )


if __name__ == "__main__":
    main()
