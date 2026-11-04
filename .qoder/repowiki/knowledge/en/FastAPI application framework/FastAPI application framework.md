---
kind: external_dependency
name: FastAPI application framework
slug: fastapi
category: external_dependency
category_hints:
    - framework_behavior
scope:
    - '**'
source_files:
    - app/main.py
    - requirements.txt
---

### FastAPI
- Role: Web framework powering the on-premise CCTV dashboard (`run.py` → `uvicorn`).
- Integration: `app/main.py` creates the Starlette/FastAPI app; routes live under `app/api/`; session + CSRF middleware is applied at startup.
- Stable shape: HTTP endpoints return JSON or render Jinja2 templates from `app/ui/templates/`; `/healthz` exposes monitoring status; `/login` redirects unauthenticated users to the login page.
- Verify exact route/middleware setup against current `app/main.py`.