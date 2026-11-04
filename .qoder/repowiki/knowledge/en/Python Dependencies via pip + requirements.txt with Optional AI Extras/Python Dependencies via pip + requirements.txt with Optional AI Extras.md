---
kind: dependency_management
name: Python Dependencies via pip + requirements.txt with Optional AI Extras
category: dependency_management
scope:
    - '**'
source_files:
    - requirements.txt
    - requirements-ai.txt
    - run.bat
    - README.md
---

## Approach

The project uses **pip** as its package manager and declares dependencies in plain-text `requirements*.txt` files under the repository root. There is no lockfile (no `requirements.lock`, `poetry.lock`, `Pipfile.lock`, etc.), no vendored third-party packages, no private registry configuration, and no dependency-update automation tooling visible in the repo.

## Key Files

- `requirements.txt` — core application dependencies (FastAPI, uvicorn, SQLAlchemy, Jinja2, python-dotenv, opencv-python, Pillow, gspread, google-auth, etc.). All pinned with `==` except `numpy>=1.26,<2.3`.
- `requirements-ai.txt` — optional AI inference dependencies (`ultralytics==8.3.40`, `insightface==0.7.3`, `onnxruntime==1.20.1`, `onnx==1.17.0`). The file header documents that these are NOT required for the dashboard shell and explains Windows build-toolchain caveats for InsightFace.
- `.venv/` — local virtual environment directory created by the bootstrap scripts; it is ignored by `.gitignore`.
- `run.bat` — Windows bootstrap script that creates a Python 3.11 venv at `.venv`, copies `.env.example` → `.env`, then runs `pip install -r requirements.txt` if FastAPI/uvicorn/sqlalchemy/cv2 are not importable.
- `README.md` — documents manual installation steps: `py -3.11 -m venv .venv`, `pip install -r requirements.txt`, and separately `pip install -r requirements-ai.txt` for camera inference.

## Architecture & Conventions

- **Two-tier dependency sets**: `requirements.txt` contains only the runtime web/dashboard stack; `requirements-ai.txt` is an opt-in extra set for YOLO detection + InsightFace face recognition. This lets users run the app without heavy ML wheels.
- **Pin policy**: Most packages use exact pins (`==x.y.z`). The one exception is `numpy>=1.26,<2.3`, which constrains the major/minor range rather than pinning a single version.
- **Virtual environments**: The project expects a per-repo `.venv` created against Python 3.11. The Windows batch script enforces this by invoking `py -3.11 -m venv .venv` and aborting with a message if Python 3.11 is not found.
- **No lockfiles or reproducible builds**: Dependency resolution is delegated to pip at install time; there is no `pip-tools`, `pipenv`, `poetry`, or `uv` lockfile to guarantee identical transitive resolutions across machines.
- **No vendoring or private registries**: No `vendor/` directory, no `setup.cfg`/`pyproject.toml` `[tool.pip]` config, no `PIP_INDEX_URL` / `PIP_EXTRA_INDEX_URL` usage, and no custom PyPI mirror configured in the visible codebase.
- **Runtime feature gating**: The AI extras are intentionally decoupled so the app can start even when they fail to install; the `requirements-ai.txt` header explicitly states that InsightFace build failures leave face recognition "unavailable" rather than crashing the server.

## Conventions & Constraints

- Dependencies are declared exclusively in `requirements.txt` (core) and `requirements-ai.txt` (optional AI). No other manifest files (`pyproject.toml`, `setup.py`, `Pipfile`) were found.
- Transitive dependencies are not pinned; only direct dependencies appear in the two requirement files.
- The bootstrap script `run.bat` installs dependencies automatically on first run but does not check for updates — it only installs if the core imports (`fastapi`, `uvicorn`, `sqlalchemy`, `cv2`) fail.
- The README documents the supported interpreter as Python 3.11 on Windows 10/11; the batch script hard-codes `py -3.11` when creating the venv.