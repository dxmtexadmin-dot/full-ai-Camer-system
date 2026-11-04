---
kind: logging_system
name: Standard Library Logging with Rotating File Sinks
category: logging_system
scope:
    - '**'
source_files:
    - app/utils/logging_config.py
    - app/main.py
    - app/api/auth.py
    - app/api/cameras.py
    - app/api/deps.py
    - app/api/employees.py
    - app/api/equipment.py
    - app/api/events.py
    - app/api/pages.py
    - app/api/reports.py
    - app/api/settings.py
    - app/database.py
    - app/services/bootstrap.py
    - app/services/camera_service.py
    - app/services/event_service.py
---

## Overview

The application uses Python's standard library `logging` module exclusively — no third-party logging frameworks (structlog, loguru, python-json-logger, etc.) are involved. A single centralized configuration function in `app/utils/logging_config.py` sets up two sinks: console output via `StreamHandler(sys.stdout)` and a rotating file handler writing to `logs/app.log`. All other modules obtain their logger through `logging.getLogger(__name__)`, giving each module its own hierarchical logger name.

## Configuration Entry Point

`setup_logging(log_level="INFO", log_dir=None)` is called once from `app.main.lifespan` during FastAPI startup:

```python
setup_logging(settings.log_level, settings.log_dir)
```

Key behaviors of `setup_logging`:
- Idempotent via a `_CONFIGURED` global flag; subsequent calls are no-ops.
- Sets the root logger level from the string value (`getattr(logging, str(log_level).upper(), logging.INFO)`).
- Applies a single formatter to both handlers:
  - Format: `"%(asctime)s | %(levelname)-8s | %(name)-38s | %(message)s"`
  - Date format: `%Y-%m-%d %H:%M:%S`
- Console sink: `StreamHandler(stream=sys.stdout)`.
- File sink: `RotatingFileHandler("logs/app.log", maxBytes=5*1024*1024, backupCount=5, encoding="utf-8")`; creates the directory if missing; falls back gracefully on `OSError` by emitting a warning.
- Silences noisy third-party libraries at DEBUG level by explicitly setting `uvicorn.access`, `watchfiles`, and `multipart` to `WARNING`.
- A `reset_logging()` helper clears the `_CONFIGURED` flag for tests.

## Log Levels Used Across the Codebase

All modules follow the same pattern: import `logging`, then `logger = logging.getLogger(__name__)`, and call one of the five levels depending on severity:

| Level | Usage examples |
|-------|---------------|
| `DEBUG` | Duplicate event suppression in `event_service.py` |
| `INFO` | Lifecycle events (startup/shutdown), camera status transitions, employee creation, database schema ready, equipment seeding |
| `WARNING` | CSRF failures, config warnings, validation errors, failed file/folder deletions, camera read errors |
| `ERROR` | Camera open failures |
| `EXCEPTION` | Unhandled exception catch-all in the FastAPI exception handler, plus camera callback exceptions |

No module uses `CRITICAL`; error conditions are split between `ERROR` (expected failure paths) and `EXCEPTION` (unexpected tracebacks).

## Log Output Structure

There is no structured JSON logging. Logs are plain text lines with a fixed pipe-delimited layout:

```
2026-10-27 12:34:56 | INFO     | app.services.camera_service          | Kamera ishga tushdi: CAM01 (rtsp)
```

Fields are always: timestamp, level (left-padded to 8 chars), logger name (left-padded to 38 chars), message. The logger name encodes the module path (e.g. `app.api.auth`, `app.services.camera_service`).

## Sinks and Routing

- **Console**: stdout, visible when running the app directly or via `run.py` / `run.sh` / `run.bat`.
- **File**: `logs/app.log`, rotated at 5 MB with up to 5 backups (`app.log`, `app.log.1`, … `app.log.5`).
- There is no per-module routing beyond the standard logger hierarchy; all handlers sit on the root logger.
- No separate access/error log files; HTTP request logs from Uvicorn are suppressed to `WARNING` rather than written to a dedicated access log.

## Conventions Observed

- Every module that logs follows the same three-line idiom: `import logging`, then `logger = logging.getLogger(__name__)`, then `logger.<level>(...)`.
- Log messages are written in Uzbek (e.g. `"Kamera ochilmadi (%s): %s"`, `"Tizim tayyor: http://%s:%s"`) rather than English.
- Parameters are passed as positional args to the logger (e.g. `logger.info("...", arg1, arg2)`) so formatting happens inside the logging framework only when the message is actually emitted — this avoids unnecessary string interpolation at disabled log levels.
- Exceptions are logged with `logger.exception(...)` at the top-level unhandled exception handler and in camera callbacks, which automatically appends the traceback to the file sink.
- The `logs/` directory is created lazily by the rotating file handler; it does not need to exist before startup.
- Tests can reconfigure logging by calling `reset_logging()` before invoking `setup_logging` again.

## Constraints Enforced by the Implementation

- `setup_logging` is intentionally single-call: the `_CONFIGURED` guard prevents double configuration, which would otherwise duplicate handlers on the root logger.
- Only the root logger is configured; child loggers inherit its handlers and level, so there is no way to route logs from specific modules to different sinks without modifying `setup_logging`.
- The log level is resolved from a string via `getattr(logging, ...)`, so invalid level names silently fall back to `INFO` rather than raising.