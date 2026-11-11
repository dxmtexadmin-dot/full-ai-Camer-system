"""Ishlab turgan paneldan kamera kadrlarini yig'ish (dataset uchun).

Nega API orqali: kamera `CameraWorker` tomonidan band qilingan. USB kamerani
ikkinchi jarayonda to'g'ridan-to'g'ri `cv2.VideoCapture` bilan ochish ishlab
turgan pipeline bilan to'qnashadi. Shuning uchun kadrlar ilovaning o'z
`/api/cameras/<kod>/snapshot` endpoint'idan olinadi — bu xavfsiz va kadr aynan
production pipeline ko'radigan ko'rinishda bo'ladi (burchak, yorug'lik, o'lcham).

Faqat standart kutubxona + Pillow + numpy ishlatiladi (og'ir ML paketlari shart emas).

Misollar
--------
    python ai_training/scripts/collect_frames.py --camera 101 --count 120
    python ai_training/scripts/collect_frames.py --camera 101 --count 300 --interval 1.0 --min-diff 6

Parol `.env` dagi `BOOTSTRAP_ADMIN_PASSWORD` dan olinadi (yoki --password).
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from pathlib import Path

import numpy as np
from PIL import Image

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = PROJECT_ROOT / "ai_training" / "dataset" / "raw"
CSRF_RE = re.compile(r'name="csrf_token"[^>]*value="([^"]+)"')


def read_env(path: Path) -> dict[str, str]:
    """.env faylini oddiy `KEY=VALUE` ko'rinishida o'qish."""
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


class Panel:
    """Panel bilan ishlash uchun minimal HTTP klient (cookie saqlaydi)."""

    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.username = username
        self.password = password
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(CookieJar())
        )

    def _get(self, path: str) -> bytes:
        with self.opener.open(f"{self.base_url}{path}", timeout=30) as response:
            return response.read()

    def login(self) -> None:
        page = self._get("/login").decode("utf-8", errors="replace")
        match = CSRF_RE.search(page)
        if not match:
            raise SystemExit("Login sahifasida csrf_token topilmadi.")
        payload = urllib.parse.urlencode(
            {
                "username": self.username,
                "password": self.password,
                "next": "/",
                "csrf_token": match.group(1),
            }
        ).encode()
        request = urllib.request.Request(f"{self.base_url}/login", data=payload)
        with self.opener.open(request, timeout=30) as response:
            body = response.read().decode("utf-8", errors="replace")
        if "/login" in response.geturl() or "Kirish" in body[:2000]:
            raise SystemExit("Login muvaffaqiyatsiz — foydalanuvchi yoki parol noto'g'ri.")

    def snapshot(self, camera: str) -> bytes:
        return self._get(f"/api/cameras/{camera}/snapshot")


def signature(payload: bytes) -> np.ndarray:
    """Kadrni 32x32 kulrang "barmoq izi" ga keltirish (o'zgarishni o'lchash uchun)."""
    image = Image.open(io.BytesIO(payload)).convert("L").resize((32, 32))
    return np.asarray(image, dtype=np.float32)


def main(argv: list[str] | None = None) -> int:
    env = read_env(PROJECT_ROOT / ".env")
    parser = argparse.ArgumentParser(description="Kamera kadrlarini dataset uchun yig'ish")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--camera", required=True, help="kamera kodi (masalan 101)")
    parser.add_argument("--count", type=int, default=120, help="nechta kadr saqlanadi")
    parser.add_argument("--interval", type=float, default=1.5, help="kadrlar orasidagi soniya")
    parser.add_argument(
        "--min-diff",
        type=float,
        default=5.0,
        help="oldingi kadrdan farq (0-255) shundan kam bo'lsa saqlanmaydi",
    )
    parser.add_argument("--timeout", type=float, default=600.0, help="umumiy vaqt chegarasi")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="saqlash papkasi")
    parser.add_argument("--session", default="", help="sessiya nomi (standart: vaqt belgisi)")
    parser.add_argument("--username", default=env.get("BOOTSTRAP_ADMIN_USERNAME", "admin"))
    parser.add_argument("--password", default=env.get("BOOTSTRAP_ADMIN_PASSWORD", ""))
    args = parser.parse_args(argv)

    if not args.password:
        print("Parol topilmadi: .env dagi BOOTSTRAP_ADMIN_PASSWORD yoki --password.")
        return 2

    session = args.session or time.strftime("session_%Y%m%d_%H%M%S")
    target = Path(args.out) / session
    target.mkdir(parents=True, exist_ok=True)

    panel = Panel(args.base_url, args.username, args.password)
    try:
        panel.login()
    except urllib.error.URLError as exc:
        print(f"Panelga ulanib bo'lmadi ({args.base_url}): {exc}")
        return 2

    saved = 0
    attempts = 0
    last = None
    started = time.time()
    print(f"Yig'ilmoqda: kamera {args.camera} -> {target}")
    print(f"Maqsad: {args.count} kadr, interval {args.interval}s, min farq {args.min_diff}")

    while saved < args.count and time.time() - started < args.timeout:
        attempts += 1
        try:
            payload = panel.snapshot(args.camera)
        except urllib.error.HTTPError as exc:
            print(f"  kadr olinmadi (HTTP {exc.code}) — kamera holatini tekshiring")
            time.sleep(args.interval)
            continue
        except urllib.error.URLError as exc:
            print(f"  tarmoq xatosi: {exc}")
            time.sleep(args.interval)
            continue

        current = signature(payload)
        if last is not None and float(np.abs(current - last).mean()) < args.min_diff:
            time.sleep(min(args.interval, 0.5))
            continue

        saved += 1
        name = f"frame_{saved:04d}.jpg"
        (target / name).write_bytes(payload)
        last = current
        if saved % 10 == 0 or saved == 1:
            image = Image.open(io.BytesIO(payload))
            print(f"  {name}  {image.width}x{image.height}  ({saved}/{args.count})")
        time.sleep(args.interval)

    meta = {
        "session": session,
        "camera": args.camera,
        "base_url": args.base_url,
        "frames": saved,
        "attempts": attempts,
        "interval": args.interval,
        "min_diff": args.min_diff,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "seconds": round(time.time() - started, 1),
    }
    (target / "session.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"\nSaqlandi: {saved} kadr ({attempts} urinish, {meta['seconds']}s)")
    print(f"Metadata: {target / 'session.json'}")
    if saved < args.count:
        print("Diqqat: maqsadga yetilmadi — kamera sekin yoki sahna o'zgarmayapti.")
    return 0 if saved else 1


if __name__ == "__main__":
    sys.exit(main())
