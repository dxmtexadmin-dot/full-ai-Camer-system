---
kind: build_system
name: Python Virtualenv-Based Build & Launch Scripts (No CI/Containerization)
category: build_system
scope:
    - '**'
source_files:
    - run.sh
    - run.bat
    - requirements.txt
    - requirements-ai.txt
    - pytest.ini
    - .env.example
    - run.py
---

# Build System

## Approach

The project has **no formal build system** — no Makefile, Dockerfile, pyproject.toml, setup.py, tox.ini, or CI configuration files exist in the repository. The "build" is essentially a Python application launched via two thin bootstrap scripts that:

1. Create a `.venv` virtual environment if missing.
2. Copy `.env.example` → `.env` if `.env` is absent.
3. Check whether core packages (`fastapi`, `uvicorn`, `sqlalchemy`, `cv2`) are importable; if not, run `pip install -r requirements.txt`.
4. `exec` into `run.py` with all forwarded arguments.

This is a **developer-experience convenience wrapper**, not a reproducible build pipeline.

## Key Files

- `run.sh` — POSIX shell entry point (Linux/macOS). Uses `set -euo pipefail`, prefers `python3.11`, falls back to `python3`.
- `run.bat` — Windows batch entry point. Prefers `py -3.11`, falls back to `python`. Includes explicit error text directing users to install Python 3.11 and enable "Add python.exe to PATH".
- `requirements.txt` — pinned core dependencies (FastAPI 0.115.14, Uvicorn 0.32.1, SQLAlchemy 2.0.55, OpenCV 4.10.0.84, etc.). Comment states "Verified with Python 3.11 on Windows 10/11".
- `requirements-ai.txt` — optional AI inference stack (ultralytics 8.3.40, insightface 0.7.3, onnxruntime 1.20.1, onnx 1.17.0). Documented as NOT required for the dashboard/inventory UI; only needed for real camera YOLO + InsightFace inference.
- `pytest.ini` — test discovery: `testpaths = tests`, excludes `.venv .git data logs secrets scripts app/ui`, pattern `test_*.py`, default `-ra`.
- `.env.example` — template for runtime secrets (copied by both scripts).
- `run.py` — actual FastAPI application entry point invoked by the wrappers.

## Architecture & Conventions

- **Virtualenv-first**: Both scripts create `.venv` at the repo root rather than relying on a global interpreter. The venv path is platform-specific: `.venv/bin/python` (POSIX) vs `.venv\Scripts\python.exe` (Windows).
- **Dependency partitioning**: Core runtime deps live in `requirements.txt`; heavy ML/AI deps are split out into `requirements-ai.txt` so the dashboard can run without GPU/CPU-heavy wheels.
- **Environment bootstrapping**: `.env` is auto-copied from `.env.example` on first run, with inline instructions to change `APP_SECRET_KEY` and `BOOTSTRAP_ADMIN_PASSWORD`.
- **Runtime check before install**: Instead of always reinstalling, scripts probe for `import fastapi, uvicorn, sqlalchemy, cv2` to skip pip when dependencies are already present.
- **Test isolation**: `pytest.ini` explicitly ignores `scripts/` and `app/ui/` from test discovery, keeping CLI helpers and templates out of the test suite.

## Conventions & Constraints

- **Python version**: The scripts and comments target Python 3.11. `run.sh` checks for `python3.11` first; `run.bat` tries `py -3.11` then `python`. If neither exists, both exit with an error instructing the user to install Python 3.11.
- **Pinned core dependencies**: `requirements.txt` pins exact versions for most packages (e.g., `fastapi==0.115.14`, `opencv-python==4.10.0.84`). Only `numpy` uses a range (`>=1.26,<2.3`).
- **Optional AI stack**: `requirements-ai.txt` is documented as optional; the application starts even if these packages fail to install, reporting face recognition as "unavailable" (per the comment in `requirements-ai.txt`).
- **No containerization**: There is no `Dockerfile`, `docker-compose.yml`, or any container-related build artifact in the repository.
- **No CI/CD**: No `.github/`, `.gitlab-ci.yml`, `.circleci/`, or similar CI configuration exists.
- **No packaging metadata**: No `setup.py`, `setup.cfg`, `pyproject.toml`, or `MANIFEST.in` — the project is not published as a PyPI package.
- **No Makefile / build orchestration**: All orchestration lives in the two shell/batch scripts; there is no task runner, linter config, or formatter invocation in the build surface.