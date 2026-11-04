---
kind: error_handling
name: FastAPI Exception Handlers and Custom Sentinel Errors
category: error_handling
scope:
    - '**'
source_files:
    - app/main.py
    - app/api/deps.py
    - app/ui/templates/error.html
---

## Approach

The application uses FastAPI's exception-handler mechanism combined with a small set of custom sentinel exceptions defined in `app/api/deps.py`. There is no dedicated `errors/` package, error-code enum, or third-party error library — error handling is centralized in the app factory (`app/main.py`) and the dependency layer (`app/api/deps.py`).

## Key Files

- `app/main.py` — registers all exception handlers on the `FastAPI` instance inside `create_app()`.
- `app/api/deps.py` — defines the two custom sentinel exceptions `AuthRequired` and `Forbidden`, plus CSRF validation that raises FastAPI's `HTTPException`.
- `app/ui/templates/error.html` — single Jinja2 template used for all non-API error pages.

## Architecture and Conventions

### Custom sentinel exceptions (in `app/api/deps.py`)

Two lightweight `Exception` subclasses are used as domain-level signals:

- `AuthRequired(next_url: str = "/")` — raised by `require_user()` when no authenticated session exists; carries a `next_url` attribute so the handler can redirect back after login.
- `Forbidden(message: str = "Bu amal uchun ruxsat yo'q.")` — raised by `require_roles()` / `require_admin()` / `require_operator()` when the user lacks the required role; carries a human-readable `message`.

These are not caught locally in route handlers; they bubble up to the top-level handlers in `main.py`.

### Centralized exception handlers (in `app/main.py`)

Five handlers are registered via `@app.exception_handler(...)`:

1. **`AuthRequired`** → `303 RedirectResponse` to `/login?next=<url>`.
2. **`Forbidden`** → renders `error.html` with title `"Ruxsat yo'q"` and status `403`.
3. **`StarletteHTTPException`** (FastAPI's built-in) → branch on request path:
   - `/api/*` routes return plain JSON `{"detail": ...}`.
   - All other routes render `error.html`; messages under HTTP 5xx are replaced with the generic `"Server xatosi."` so internal details are never leaked to the browser.
4. **`RequestValidationError`** → logs a warning and returns JSON `{"detail": "Kiritilgan ma'lumotlar noto'g'ri.", "errors": [...]}` with status `422`, truncating errors to 5 entries.
5. **`Exception`** (catch-all) → logs via `logger.exception(...)`, then returns either a sanitized JSON `500` for `/api/*` or an HTML `error.html` page whose message points users to the log file.

This last handler is marked `# pragma: no cover - safety net`, indicating it is intentionally kept as a final guard against unhandled exceptions.

### CSRF and input-validation errors

CSRF failures go through FastAPI's `HTTPException(status_code=400, detail="CSRF token noto'g'ri yoki muddati o'tgan.")` from `verify_csrf()` in `deps.py`, which is then handled by the generic `StarletteHTTPException` handler above.

Route handlers also raise raw `ValueError` in several places (e.g. `app/api/equipment.py` lines 245–255, `app/api/employees.py`, `app/api/settings.py`, `app/config.py`). These are typically caught locally with `except ValueError as exc:` and re-raised as business-domain errors or converted into flash notices — they do not propagate to the global handlers unless uncaught.

### Error presentation

- API endpoints (`/api/*`) always receive JSON responses — never stack traces or internal messages.
- Browser-facing routes render `error.html`, which shows a code/title line, a message paragraph, and two navigation buttons (home + back).
- The template receives `request`, `title`, `message`, and `user` context variables from every handler.

## Conventions and Constraints

- **Sentinel exceptions over string codes**: authorization failures use `AuthRequired` / `Forbidden` rather than numeric error codes or string constants.
- **No stack traces to clients**: both the `StarletteHTTPException` and catch-all `Exception` handlers replace server-side details with safe messages for non-API routes.
- **API vs. web-route bifurcation**: every handler checks `request.url.path.startswith("/api/")` to decide between JSON and HTML output — this is the repository-wide convention for dual-mode responses.
- **Validation errors are logged before responding**: `RequestValidationError` handler emits a `logger.warning` before returning the 422 JSON.
- **Uncaught exceptions are logged with full traceback**: the catch-all handler uses `logger.exception(...)` so the server logs retain diagnostics even though the client sees only a sanitized message.
- **CSRF failures use FastAPI's `HTTPException` directly** rather than a custom sentinel, since they map one-to-one to an HTTP 400 response.