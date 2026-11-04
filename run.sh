#!/usr/bin/env bash
# ============================================================================
#  AI CCTV inventar tizimi - Linux / macOS ishga tushirish skripti
#  Birinchi marta: virtual muhit yaratiladi, paketlar o'rnatiladi, .env tayyorlanadi.
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

VENV_PY=".venv/bin/python"

if [ ! -x "$VENV_PY" ]; then
    echo "[1/3] Virtual muhit yaratilmoqda (.venv)..."
    if command -v python3.11 >/dev/null 2>&1; then
        python3.11 -m venv .venv
    elif command -v python3 >/dev/null 2>&1; then
        python3 -m venv .venv
    else
        echo "XATO: python3 topilmadi. Python 3.11 ni o'rnating." >&2
        exit 1
    fi
fi

if [ ! -f ".env" ]; then
    echo "[2/3] .env fayli .env.example dan yaratilmoqda..."
    cp .env.example .env
    echo "      .env ichidagi APP_SECRET_KEY va BOOTSTRAP_ADMIN_PASSWORD ni o'zgartiring."
fi

if ! "$VENV_PY" -c "import fastapi, uvicorn, sqlalchemy, cv2" >/dev/null 2>&1; then
    echo "[3/3] Kerakli paketlar o'rnatilmoqda..."
    "$VENV_PY" -m pip install --upgrade pip --quiet
    "$VENV_PY" -m pip install -r requirements.txt
else
    echo "[3/3] Paketlar joyida."
fi

echo
echo "==========================================================================="
echo " Server ishga tushmoqda. Brauzerda http://127.0.0.1:8000 ni oching."
echo " To'xtatish uchun Ctrl+C."
echo "==========================================================================="
echo

exec "$VENV_PY" run.py "$@"
