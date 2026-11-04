@echo off
rem ===========================================================================
rem  AI CCTV inventar tizimi - Windows ishga tushirish skripti
rem  Birinchi marta: virtual muhit yaratiladi, paketlar o'rnatiladi, .env tayyorlanadi.
rem ===========================================================================
setlocal
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"

if not exist "%VENV_PY%" goto :create_venv
goto :check_env

:create_venv
echo [1/3] Virtual muhit yaratilmoqda (.venv)...
py -3.11 -m venv .venv 2>nul
if not exist "%VENV_PY%" (
    python -m venv .venv 2>nul
)
if not exist "%VENV_PY%" (
    echo.
    echo XATO: Python 3.11 topilmadi.
    echo Python 3.11 ni o'rnating: https://www.python.org/downloads/
    echo O'rnatishda "Add python.exe to PATH" bandini belgilang.
    echo.
    pause
    exit /b 1
)

:check_env
if not exist ".env" (
    echo [2/3] .env fayli .env.example dan yaratilmoqda...
    copy /y ".env.example" ".env" >nul
    echo      .env ichidagi APP_SECRET_KEY va BOOTSTRAP_ADMIN_PASSWORD ni o'zgartiring.
)

"%VENV_PY%" -c "import fastapi, uvicorn, sqlalchemy, cv2" >nul 2>&1
if errorlevel 1 (
    echo [3/3] Kerakli paketlar o'rnatilmoqda...
    "%VENV_PY%" -m pip install --upgrade pip --quiet
    "%VENV_PY%" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo.
        echo XATO: paketlarni o'rnatib bo'lmadi. Internet aloqasini tekshiring.
        pause
        exit /b 1
    )
) else (
    echo [3/3] Paketlar joyida.
)

echo.
echo ===========================================================================
echo  Server ishga tushmoqda. Brauzerda http://127.0.0.1:8000 ni oching.
echo  To'xtatish uchun Ctrl+C.
echo ===========================================================================
echo.

"%VENV_PY%" run.py %*

echo.
echo Server to'xtadi.
pause
