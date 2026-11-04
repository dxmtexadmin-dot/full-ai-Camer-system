---
kind: configuration_system
name: Environment-Driven Settings via Frozen Dataclass + Whitelisted Runtime Editing
category: configuration_system
scope:
    - '**'
source_files:
    - app/config.py
    - .env.example
    - app/services/settings_service.py
    - app/main.py
---

## Approach

The application uses a **12-factor-style environment-variable configuration** backed by `python-dotenv`. There is no YAML/JSON/TOML config file; every setting is an uppercase `ENV_VAR` read from `os.environ`, optionally pre-populated from a local `.env` file. The single source of truth for defaults and types lives in `app/config.py`.

## Key Files

- `app/config.py` — core loader, type helpers, frozen `Settings` dataclass, process-wide cache (`lru_cache(maxsize=1)`).
- `.env.example` — the canonical list of all supported variables with comments describing each one.
- `app/services/settings_service.py` — runtime-editable subset of settings exposed through the UI's Sozlamalar (Settings) page.
- `app/main.py` — calls `get_settings()` at startup, applies `settings.warnings()` as log warnings, and wires `secret_key` / `session_max_age` / `is_production` into FastAPI middleware.
- `secrets/` directory — reserved for Google service-account JSON files referenced by `GOOGLE_SERVICE_ACCOUNT_FILE`.

## Architecture & Conventions

### Loading pipeline

1. On import, `app/config.py` runs `load_env_file()`, which loads `.env` via `dotenv.load_dotenv(override=False)` if it exists under the project root (`PROJECT_ROOT = Path(__file__).resolve().parent.parent`).
2. `get_settings()` builds a `Settings` instance by reading each variable through small typed helpers:
   - `_str(name, default)` — strips whitespace, returns default when missing/empty.
   - `_int(name, default)` — parses int, falls back to default on `ValueError`.
   - `_float(name, default)` — same for floats.
   - `_bool(name, default)` — treats `"1"|"true"|"yes"|"on"` (case-insensitive) as true.
   - `_path(name, default)` — resolves relative paths against `PROJECT_ROOT`.
3. `@lru_cache(maxsize=1)` memoizes the result; `reload_settings()` clears the cache so callers can refresh after `.env` changes.
4. `Settings` is declared `frozen=True` (immutable snapshot). Directory fields (`data_dir`, `employees_dir`, `evidence_dir`, `models_dir`, `log_dir`, `secrets_dir`) are computed from `data_dir` or `LOG_DIR`.
5. `ensure_directories()` creates the required directories lazily.
6. `warnings()` emits operator-facing diagnostics (default secret key, default bootstrap password, missing YOLO model, incomplete Google Sheets config).

### Runtime editing (whitelist)

`app/services/settings_service.py` defines a `FIELD_MAP` of ~19 whitelisted keys that may be edited through the web UI. Each entry carries:
- `key` (env var name),
- `label_uz` (Uzbek label for the form),
- `kind` (`bool|int|float|str`),
- `group` (UI grouping: detection/camera/face/events/sheets),
- optional `minimum`/`maximum` bounds,
- `help_uz` help text.

`update_env(values)` normalizes inputs via `_normalise`, writes only changed keys to `.env` atomically (write to `.tmp` then `Path.replace`), updates `os.environ` directly (since `dotenv` does not override existing vars), calls `reload_settings()`, and finally pushes the new `Settings` object into long-lived services (`monitor.settings`, `yolo.model_path`, `monitor.face_service.settings`, `monitor.sheets_service.settings`) and restarts pipelines via `monitor.reload_all()`.

Secrets are intentionally excluded from the editable whitelist: `APP_SECRET_KEY`, `DATABASE_URL`, and bootstrap admin credentials cannot be changed from the UI.

### Production gating

`Settings.is_production` checks `env.lower() in {"prod", "production"}`. This flag is used in `app/main.py` to:
- disable OpenAPI docs (`docs_url="/api/docs" if not settings.is_production else None`),
- set `SessionMiddleware.https_only=True`.

### Default values

Defaults are embedded in `_build_settings()` and mirrored in `.env.example`. Representative examples:
- `APP_ENV=development`, `APP_HOST=127.0.0.1`, `APP_PORT=8000`, `APP_TIMEZONE=Asia/Tashkent`
- `DATABASE_URL=sqlite:///./data/inventory.db`
- `YOLO_MODEL_PATH=data/models/equipment_best.pt`, `YOLO_DEVICE=cpu`, `YOLO_CONFIDENCE=0.35`
- `FACE_MODEL_NAME=buffalo_l`, `FACE_SIMILARITY_THRESHOLD=0.45`, `FACE_PROVIDERS=CPUExecutionProvider`
- `EVIDENCE_ENABLED=true`, `EVIDENCE_RETENTION_DAYS=30`, `EVIDENCE_JPEG_QUALITY=85`
- `GOOGLE_SHEETS_ENABLED=false`, `GOOGLE_SHEETS_SYNC_INTERVAL_SECONDS=120`
- `LOG_LEVEL=INFO`, `LOG_DIR=logs`

## Conventions & Constraints

- **All configuration comes from environment variables.** `app/config.py` docstring states: "All settings come from environment variables (optionally loaded from a local `.env` file). Nothing secret is hard-coded." Hardcoded defaults exist only as fallbacks in `_build_settings()`.
- **`.env` is never committed.** `.env.example` header says "NEVER commit the real .env"; `.gitignore` excludes `.env`.
- **Settings are immutable once built.** `Settings` is a `frozen=True` dataclass; mutation goes through `reload_settings()` which rebuilds the cached instance.
- **Only whitelisted keys are editable at runtime.** `settings_service._normalise` raises `ValueError(f"'{key}' ni tahrirlash mumkin emas.")` for any key not in `FIELD_MAP`; secrets (`APP_SECRET_KEY`, `DATABASE_URL`, bootstrap passwords) are deliberately absent from the whitelist.
- **Boolean env vars accept multiple synonyms.** `_bool` recognizes `"1"|"true"|"yes"|"on"` case-insensitively; the UI normalizes booleans to lowercase `"true"|"false"` strings.
- **Numeric bounds are enforced per-field.** Each `Field` may declare `minimum`/`maximum`; `_normalise` raises a localized error when violated.
- **Paths are resolved relative to `PROJECT_ROOT`.** `_path` converts relative values to absolute paths anchored at the repo root; `google_service_account_file` follows the same rule.
- **Directories are created on demand.** `Settings.ensure_directories()` creates `data_dir`, `employees_dir`, `evidence_dir`, `models_dir`, `log_dir`, `secrets_dir` with `parents=True, exist_ok=True`.
- **Startup validates configuration before serving.** `main.lifespan` iterates `settings.warnings()` and logs them; production mode disables `/api/docs` and enables HTTPS-only sessions.
- **Runtime edits trigger a full pipeline reload.** `refresh_runtime` replaces `monitor.settings`, invalidates the YOLO model cache (`yolo._model = None`), and calls `monitor.reload_all()` to restart camera workers with new parameters.