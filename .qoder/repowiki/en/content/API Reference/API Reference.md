# API Reference

<cite>
**Referenced Files in This Document**
- [main.py](file://app/main.py)
- [config.py](file://app/config.py)
- [deps.py](file://app/api/deps.py)
- [auth.py](file://app/api/auth.py)
- [cameras.py](file://app/api/cameras.py)
- [employees.py](file://app/api/employees.py)
- [equipment.py](file://app/api/equipment.py)
- [events.py](file://app/api/events.py)
- [pages.py](file://app/api/pages.py)
- [reports.py](file://app/api/reports.py)
- [settings.py](file://app/api/settings.py)
- [README.md](file://README.md)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [Project Structure](#project-structure)
3. [Core Components](#core-components)
4. [Architecture Overview](#architecture-overview)
5. [Detailed Component Analysis](#detailed-component-analysis)
6. [Dependency Analysis](#dependency-analysis)
7. [Performance Considerations](#performance-considerations)
8. [Troubleshooting Guide](#troubleshooting-guide)
9. [Conclusion](#conclusion)
10. [Appendices](#appendices)

## Introduction
This document provides comprehensive API documentation for the face-date system’s RESTful endpoints and streaming interfaces. It covers authentication, cameras, employees, equipment, events, pages, reports, and settings. Each endpoint group documents HTTP methods, URL patterns, required headers, query parameters, request body schemas, response formats, and error codes. Authentication uses session cookies with CSRF protection for state-changing operations. The system also exposes MJPEG streaming endpoints for live camera feeds and secure file access endpoints for evidence retrieval with authorization checks.

The application is a FastAPI service that mounts routers for pages, authentication, employees, equipment, cameras, events, reports, and settings. Session middleware manages cookies, while shared dependencies enforce role-based access control (admin, operator, viewer), CSRF validation, and rendering helpers.

## Project Structure
The API surface is organized by feature modules under `app/api`. The main application wires these routers into a single FastAPI app, adds session middleware, static files, exception handlers, and a health check endpoint. Configuration is loaded from environment variables and exposed through a settings object used across services and APIs.

```mermaid
graph TB
App["FastAPI App<br/>Session Middleware<br/>Exception Handlers"] --> Pages["Pages Router"]
App --> Auth["Auth Router"]
App --> Employees["Employees Router"]
App --> Equipment["Equipment Router"]
App --> Cameras["Cameras Router"]
App --> Events["Events Router"]
App --> Reports["Reports Router"]
App --> Settings["Settings Router"]
Pages --> Deps["Shared Dependencies"]
Auth --> Deps
Employees --> Deps
Equipment --> Deps
Cameras --> Deps
Events --> Deps
Reports --> Deps
Settings --> Deps
```

**Diagram sources**
- [main.py:44-68](file://app/main.py#L44-L68)
- [deps.py:104-123](file://app/api/deps.py#L104-L123)

**Section sources**
- [main.py:44-68](file://app/main.py#L44-L68)
- [config.py:177-222](file://app/config.py#L177-L222)

## Core Components
- Authentication and session management: login/logout, user administration, CSRF token handling, and role enforcement.
- Camera management: CRUD for camera configurations, toggle enable/disable, and live MJPEG streaming plus snapshot and status endpoints.
- Employee management: CRUD for employees, reference photo upload, restricted photo access, and gallery refresh triggers.
- Equipment and inventory: asset CRUD, category configuration, manual inventory operations (receive/remove/return/transfer/initial/correct).
- Events and review: movement history, pending reviews, evidence file access, JSON event listing, and search helpers.
- Reports and export: filtered reports page, CSV export, Google Sheets sync and status.
- Settings: environment-driven configuration editing, model reload, gallery reload, and user administration.

Key security mechanisms:
- Session cookie named `cctv_session` managed via FastAPI SessionMiddleware.
- CSRF tokens validated on all POST endpoints using form fields.
- Role-based access control: admin, operator, viewer enforced via shared dependencies.
- Evidence file access restricted to operators/admins with path traversal protection.

**Section sources**
- [main.py:55-62](file://app/main.py#L55-L62)
- [deps.py:75-90](file://app/api/deps.py#L75-L90)
- [deps.py:104-123](file://app/api/deps.py#L104-L123)
- [events.py:132-144](file://app/api/events.py#L132-L144)

## Architecture Overview
The API architecture centers around FastAPI routers grouped by domain. Shared dependencies provide authentication, authorization, CSRF validation, templating, and utility functions. Services encapsulate business logic (camera monitoring, face recognition, YOLO detection, inventory ledger, Google Sheets sync). Data persistence uses SQLAlchemy with SQLite by default.

```mermaid
sequenceDiagram
participant Client as "Client"
participant App as "FastAPI App"
participant Router as "Feature Router"
participant Deps as "Shared Dependencies"
participant Service as "Business Service"
participant DB as "Database"
Client->>App : HTTP Request
App->>Router : Route Handler
Router->>Deps : require_user / require_admin / verify_csrf
Deps-->>Router : User + CSRF Validated
Router->>Service : Business Operation
Service->>DB : Query/Write
DB-->>Service : Result
Service-->>Router : Response Data
Router-->>Client : JSON/HTML/File/Stream
```

**Diagram sources**
- [main.py:44-68](file://app/main.py#L44-L68)
- [deps.py:104-123](file://app/api/deps.py#L104-L123)

## Detailed Component Analysis

### Authentication Endpoints
Authentication endpoints manage user sessions and user administration. They use HTML responses for UI flows and redirect after successful actions. All state-changing POST endpoints require CSRF tokens.

- GET `/login`
  - Purpose: Render login page; redirects to home if already authenticated.
  - Headers: None required.
  - Query Parameters: `next` (optional) — target URL after login.
  - Response: HTML login template.
  - Errors: Redirects to home if already logged in.

- POST `/login`
  - Purpose: Authenticate user and create session.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `username`, `password`, `next`, `csrf_token`.
  - Response: Redirect to `next` or home.
  - Errors:
    - 429 Too Many Requests: Brute-force guard triggered.
    - 401 Unauthorized: Invalid credentials or inactive user.
  - Security: Validates CSRF token, clears old session, sets session keys for user ID and role, ensures CSRF token presence.

- POST `/logout`
  - Purpose: Clear session and redirect to login.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `csrf_token`.
  - Response: Redirect to `/login`.
  - Errors: Redirect regardless of CSRF validity for safety.

- POST `/api/users`
  - Purpose: Create new user (admin only).
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `username`, `full_name`, `password`, `role`, `csrf_token`.
  - Response: Redirect to settings page with flash message.
  - Errors: Validation errors (username length, password length, duplicate username, invalid role).

- POST `/api/users/{user_id}/password`
  - Purpose: Change password for a specific user (self or admin).
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `password`, `csrf_token`.
  - Response: Redirect to settings page with flash message.
  - Errors: Forbidden if not self or admin; validation error if password too short.

- POST `/api/users/{user_id}/toggle`
  - Purpose: Activate/deactivate user (admin only).
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `csrf_token`.
  - Response: Redirect to settings page with flash message.
  - Errors: Cannot toggle self; user not found.

Example curl calls:
- Login:
  ```bash
  curl -X POST http://localhost:8000/login \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=admin&password=Admin123!&next=/&csrf_token=TOKEN_FROM_SESSION"
  ```
- Create user:
  ```bash
  curl -X POST http://localhost:8000/api/users \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=jdoe&full_name=John Doe&password=SecurePass123&role=viewers&csrf_token=TOKEN_FROM_SESSION"
  ```

**Section sources**
- [auth.py:58-121](file://app/api/auth.py#L58-L121)
- [auth.py:127-218](file://app/api/auth.py#L127-L218)
- [deps.py:75-90](file://app/api/deps.py#L75-L90)

### Cameras Endpoints
Camera endpoints manage camera configurations and provide live streaming capabilities. Admin-only for CRUD operations; any authenticated user can access streams and snapshots.

- GET `/cameras`
  - Purpose: Render camera management page (admin only).
  - Headers: None required.
  - Response: HTML camera overview page.

- POST `/api/cameras`
  - Purpose: Create a new camera configuration.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `code`, `name`, `location`, `source_type`, `source`, `is_enabled`, `line_x1`, `line_y1`, `line_x2`, `line_y2`, `flip_line`, `confidence`, `csrf_token`.
  - Response: Redirect to `/cameras` with flash message.
  - Errors: Validation errors for code format, name, source type, coordinates; duplicate code.

- POST `/api/cameras/{camera_id}/update`
  - Purpose: Update existing camera configuration.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: Same as create, plus `csrf_token`.
  - Response: Redirect to `/cameras` with flash message.
  - Errors: Camera not found; validation errors.

- POST `/api/cameras/{camera_id}/toggle`
  - Purpose: Enable/disable camera.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `csrf_token`.
  - Response: Redirect to `/cameras` with flash message.
  - Errors: Camera not found.

- POST `/api/cameras/{camera_id}/delete`
  - Purpose: Delete camera configuration.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `csrf_token`.
  - Response: Redirect to `/cameras` with flash message.
  - Errors: Camera not found.

- GET `/api/cameras/{code}/stream`
  - Purpose: MJPEG live stream for a camera.
  - Headers: None required.
  - Response: `multipart/x-mixed-replace; boundary=frame` with JPEG frames.
  - Errors: Stream stops if worker unavailable.

- GET `/api/cameras/{code}/snapshot`
  - Purpose: Get latest annotated JPEG snapshot.
  - Headers: None required.
  - Response: `image/jpeg`.
  - Errors: Placeholder image if no frame available.

- GET `/api/cameras/{code}/status`
  - Purpose: Get runtime status and health metrics.
  - Headers: None required.
  - Response: JSON with running status, status label, last frame time, pipeline health.

Example curl calls:
- Create camera:
  ```bash
  curl -X POST http://localhost:8000/api/cameras \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "code=CAM-01&name=Entrance&location=Main Door&source_type=rtsp&source=rtsp://admin:pass@192.168.1.64:554/stream&is_enabled=true&csrf_token=TOKEN_FROM_SESSION"
  ```
- Stream:
  ```bash
  curl -N http://localhost:8000/api/cameras/CAM-01/stream
  ```
- Snapshot:
  ```bash
  curl -o snapshot.jpg http://localhost:8000/api/cameras/CAM-01/snapshot
  ```
- Status:
  ```bash
  curl http://localhost:8000/api/cameras/CAM-01/status
  ```

**Section sources**
- [cameras.py:82-269](file://app/api/cameras.py#L82-L269)
- [cameras.py:275-329](file://app/api/cameras.py#L275-L329)

### Employees Endpoints
Employee endpoints manage employee records, reference photos, and restricted photo access. Admin-only for CRUD and photo management; operators can view restricted photos.

- GET `/employees`
  - Purpose: Render employee list page (admin only).
  - Headers: None required.
  - Query Parameters: `q` (search), `active` (`all` or `active`).
  - Response: HTML employee list page.

- GET `/employees/{code}`
  - Purpose: Render employee detail page (admin only).
  - Headers: None required.
  - Response: HTML employee detail page.

- POST `/api/employees`
  - Purpose: Create new employee.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `first_name`, `last_name`, `department`, `employee_number`, `employee_code`, `notes`, `csrf_token`.
  - Response: Redirect to employee detail page with flash message.
  - Errors: Validation errors from service layer.

- POST `/api/employees/{employee_id}/update`
  - Purpose: Update employee details.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: Same as create, plus `csrf_token`.
  - Response: Redirect to employee detail page with flash message.
  - Errors: Employee not found.

- POST `/api/employees/{employee_id}/toggle`
  - Purpose: Activate/deactivate employee.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `csrf_token`.
  - Response: Redirect to employee detail page with flash message.
  - Errors: Employee not found.

- POST `/api/employees/{employee_id}/photos`
  - Purpose: Upload reference photos for an employee.
  - Headers: `Content-Type: multipart/form-data`.
  - Form Fields: `photos[]` (files), `csrf_token`.
  - Response: Redirect to employee detail page with flash messages for stored/warnings/errors.
  - Errors: No photos selected; employee not found.

- POST `/api/employees/{employee_id}/photos/{embedding_id}/delete`
  - Purpose: Delete a reference photo and its embedding.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `csrf_token`.
  - Response: Redirect to employee detail page with flash message.
  - Errors: Photo not found.

- POST `/api/employees/{employee_id}/delete`
  - Purpose: Delete employee and biometric data.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `confirm_code`, `csrf_token`.
  - Response: Redirect to `/employees` with flash message.
  - Errors: Confirmation code mismatch; employee not found.

- GET `/api/employees/{code}/photos/{filename}`
  - Purpose: Access restricted reference photo (operators/admin only).
  - Headers: None required.
  - Response: `image/jpeg` if authorized and file exists.
  - Errors: 403 Forbidden if path traversal detected; 404 Not Found if file not referenced or missing.

Example curl calls:
- Create employee:
  ```bash
  curl -X POST http://localhost:8000/api/employees \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "first_name=Ali&last_name=Valiyev&department=IT&employee_code=EMP001&csrf_token=TOKEN_FROM_SESSION"
  ```
- Upload photos:
  ```bash
  curl -X POST http://localhost:8000/api/employees/1/photos \
    -F "photos=@/path/to/photo1.jpg" \
    -F "photos=@/path/to/photo2.jpg" \
    -F "csrf_token=TOKEN_FROM_SESSION"
  ```
- Access photo:
  ```bash
  curl -o photo.jpg http://localhost:8000/api/employees/EMP001/photos/photo1.jpg
  ```

**Section sources**
- [employees.py:25-233](file://app/api/employees.py#L25-L233)
- [employees.py:239-261](file://app/api/employees.py#L239-L261)

### Equipment Endpoints
Equipment endpoints manage assets, categories, and manual inventory operations. Admin-only for asset/category CRUD; operators can perform inventory operations.

- GET `/equipment`
  - Purpose: Render equipment management page (admin only).
  - Headers: None required.
  - Query Parameters: `q` (search), `category` (filter), `status` (filter).
  - Response: HTML equipment list page.

- POST `/api/equipment`
  - Purpose: Create new equipment asset.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `category_code`, `asset_code`, `brand`, `model`, `serial_number`, `asset_tag`, `location`, `status`, `notes`, `csrf_token`.
  - Response: Redirect to `/equipment` with flash message.
  - Errors: Validation errors from service layer.

- POST `/api/equipment/{asset_id}/update`
  - Purpose: Update equipment asset.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: Same as create, plus `csrf_token`.
  - Response: Redirect to `/equipment` with flash message.
  - Errors: Asset not found; validation errors.

- POST `/api/categories/{category_id}/update`
  - Purpose: Update equipment category (labels, confidence, active status).
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `label_uz`, `yolo_labels`, `min_confidence`, `is_active`, `csrf_token`.
  - Response: Redirect to `/equipment` with flash message.
  - Errors: Category not found; at least one YOLO label required.

- GET `/inventory`
  - Purpose: Render inventory page (authenticated users; operations require operator+).
  - Headers: None required.
  - Query Parameters: `category` (filter), `confirmed` (`all`, `yes`, `no`).
  - Response: HTML inventory page with balances and ledger.

- POST `/api/inventory/operation`
  - Purpose: Perform manual inventory operation (receive/remove/return/transfer/initial/correct).
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `operation`, `category_code`, `quantity`, `delta` (for correct), `location`, `reason`, `csrf_token`.
  - Response: Redirect to `/inventory` with flash message.
  - Errors: Unknown operation; initial stock requires admin; delta required for correction.

Example curl calls:
- Create asset:
  ```bash
  curl -X POST http://localhost:8000/api/equipment \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "category_code=laptop&asset_code=ASSET-001&brand=Dell&model=XPS&serial_number=SN123&location=Office A&status=in_stock&csrf_token=TOKEN_FROM_SESSION"
  ```
- Receive item:
  ```bash
  curl -X POST http://localhost:8000/api/inventory/operation \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "operation=receive&category_code=laptop&quantity=5&location=Warehouse&reason=New shipment&csrf_token=TOKEN_FROM_SESSION"
  ```

**Section sources**
- [equipment.py:25-169](file://app/api/equipment.py#L25-L169)
- [equipment.py:175-261](file://app/api/equipment.py#L175-L261)

### Events Endpoints
Event endpoints provide movement history, review workflow, evidence access, and JSON APIs for integrations.

- GET `/movements`
  - Purpose: Render movements page (authenticated users).
  - Headers: None required.
  - Query Parameters: `page`, `start`, `end`, `camera`, `status`, `type`, `category`, `employee_id`, `q`.
  - Response: HTML movements page with paginated events.

- GET `/review`
  - Purpose: Render review page (operator/admin only).
  - Headers: None required.
  - Response: HTML review page with pending events and balances.

- POST `/api/events/{event_id}/review`
  - Purpose: Review an event (confirm/reject) and optionally associate employee/asset/category.
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `decision`, `reason`, `employee_id`, `asset_id`, `category_code`, `next_url`, `csrf_token`.
  - Response: Redirect to `next_url` or `/review` with flash message.
  - Errors: Event not found; validation errors from service layer.

- GET `/api/events/{public_id}/evidence`
  - Purpose: Retrieve evidence JPEG for an event (operator/admin only).
  - Headers: None required.
  - Response: `image/jpeg` if authorized and file exists.
  - Errors: 404 Not Found if event or evidence missing; 403 Forbidden if path traversal detected.

- GET `/api/events`
  - Purpose: Minimal read-only JSON API for integrations (no biometric data).
  - Headers: None required.
  - Query Parameters: `limit` (default 50, max 200), plus filters: `start`, `end`, `camera`, `status`, `type`, `category`, `employee_id`, `q`.
  - Response: JSON with `count` and `items` array containing event summaries.

- GET `/api/employees/search`
  - Purpose: Search active employees for integration (operator/admin only).
  - Headers: None required.
  - Query Parameters: `q` (search term).
  - Response: JSON with `items` array of `{id, code, name}`.

- GET `/api/assets/search`
  - Purpose: Search assets for integration (operator/admin only).
  - Headers: None required.
  - Query Parameters: `q` (search term), `category` (filter).
  - Response: JSON with `items` array of `{id, code, category}`.

Example curl calls:
- List events:
  ```bash
  curl "http://localhost:8000/api/events?limit=10&type=PERSON_ENTERED&status=PENDING"
  ```
- Review event:
  ```bash
  curl -X POST http://localhost:8000/api/events/123/review \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "decision=CONFIRMED&reason=Confirmed entry&employee_id=1&asset_id=5&category_code=laptop&next_url=/review&csrf_token=TOKEN_FROM_SESSION"
  ```
- Access evidence:
  ```bash
  curl -o evidence.jpg http://localhost:8000/api/events/EVT-ABC123/evidence
  ```

**Section sources**
- [events.py:40-144](file://app/api/events.py#L40-L144)
- [events.py:147-191](file://app/api/events.py#L147-L191)

### Pages Endpoints
Page endpoints render dashboard and live view pages for authenticated users.

- GET `/`
  - Purpose: Render dashboard page (authenticated users).
  - Headers: None required.
  - Response: HTML dashboard with camera stats, recent events, balances, and system status.

- GET `/live`
  - Purpose: Render live camera view page (authenticated users).
  - Headers: None required.
  - Query Parameters: `camera` (optional, selects camera).
  - Response: HTML live view page with camera selection and recent events.

Example curl call:
- Dashboard:
  ```bash
  curl http://localhost:8000/
  ```

**Section sources**
- [pages.py:25-79](file://app/api/pages.py#L25-L79)

### Reports Endpoints
Report endpoints provide filtered reports, CSV export, and Google Sheets integration.

- GET `/reports`
  - Purpose: Render reports page (authenticated users).
  - Headers: None required.
  - Query Parameters: `start`, `end`, `camera`, `category`, `status`, `type`, `employee_id`.
  - Response: HTML reports page with summaries, balances, and sync status.

- GET `/reports/export.csv`
  - Purpose: Export filtered events as CSV (UTF-8 BOM for Excel compatibility).
  - Headers: None required.
  - Query Parameters: Same as `/reports`.
  - Response: `text/csv; charset=utf-8` with attachment header.

- POST `/api/sheets/sync`
  - Purpose: Manually trigger Google Sheets synchronization (operator/admin only).
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `csrf_token`.
  - Response: Redirect to `/reports#sheets` with flash message.
  - Errors: Sync failures handled by service layer.

- GET `/api/sheets/status`
  - Purpose: Get Google Sheets sync status and pending events count.
  - Headers: None required.
  - Response: JSON with service status, pending events, and last log entry.

Example curl calls:
- Export CSV:
  ```bash
  curl -o events.csv "http://localhost:8000/reports/export.csv?start=2024-01-01&end=2024-12-31&type=PERSON_ENTERED"
  ```
- Sheets status:
  ```bash
  curl http://localhost:8000/api/sheets/status
  ```

**Section sources**
- [reports.py:43-177](file://app/api/reports.py#L43-L177)

### Settings Endpoints
Settings endpoints allow administrators to edit environment-driven configuration, reload models, and refresh galleries.

- GET `/settings`
  - Purpose: Render settings page (admin only).
  - Headers: None required.
  - Response: HTML settings page with field groups, current values, warnings, and audit logs.

- POST `/api/settings`
  - Purpose: Save settings changes (admin only).
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: Dynamic fields based on configuration schema, plus `csrf_token`.
  - Response: Redirect to `/settings` with flash message.
  - Errors: Validation errors from settings service layer.

- POST `/api/model/reload`
  - Purpose: Reload YOLO model (admin only).
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `csrf_token`.
  - Response: Redirect to `/settings` with flash message indicating success/failure.

- POST `/api/gallery/reload`
  - Purpose: Refresh face gallery (admin only).
  - Headers: `Content-Type: application/x-www-form-urlencoded`.
  - Form Fields: `csrf_token`.
  - Response: Redirect to `/settings` with flash message showing updated template count.

Example curl calls:
- Save settings:
  ```bash
  curl -X POST http://localhost:8000/api/settings \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "YOLO_CONFIDENCE=0.4&FACE_SIMILARITY_THRESHOLD=0.5&csrf_token=TOKEN_FROM_SESSION"
  ```
- Reload model:
  ```bash
  curl -X POST http://localhost:8000/api/model/reload \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "csrf_token=TOKEN_FROM_SESSION"
  ```

**Section sources**
- [settings.py:23-115](file://app/api/settings.py#L23-L115)

## Dependency Analysis
The API relies on shared dependencies for authentication, authorization, CSRF validation, templating, and utilities. Role-based access control is enforced through `require_user`, `require_admin`, and `require_operator`. CSRF tokens are generated and validated per session. File access endpoints validate paths against configured directories to prevent traversal attacks.

```mermaid
classDiagram
class Deps {
+get_csrf_token(request) string
+ensure_csrf_token(request) void
+verify_csrf(request, submitted) void
+current_user(request, db) User
+require_user(request, db) User
+require_admin(request, db) User
+require_operator(request, db) User
+render(request, template, context, user, status_code) HTMLResponse
+safe_range(start_raw, end_raw) tuple
+redirect(url, status_code) RedirectResponse
+evidence_file(relative) Path
}
class AuthRouter {
+login_page()
+login_submit()
+logout()
+create_user()
+change_password()
+toggle_user()
}
class CamerasRouter {
+cameras_page()
+create_camera()
+update_camera()
+toggle_camera()
+delete_camera()
+camera_stream()
+camera_snapshot()
+camera_status()
}
class EmployeesRouter {
+employees_page()
+employee_detail()
+create_employee()
+update_employee()
+toggle_employee()
+upload_photos()
+delete_photo()
+delete_employee()
+employee_photo()
}
class EquipmentRouter {
+equipment_page()
+create_asset()
+update_asset()
+update_category()
+inventory_page()
+inventory_operation()
}
class EventsRouter {
+movements_page()
+review_page()
+review_event_action()
+event_evidence()
+events_json()
+search_employees()
+search_assets()
}
class ReportsRouter {
+reports_page()
+export_csv()
+manual_sync()
+sheets_status()
}
class SettingsRouter {
+settings_page()
+save_settings()
+reload_model()
+reload_gallery()
}
AuthRouter --> Deps : "uses"
CamerasRouter --> Deps : "uses"
EmployeesRouter --> Deps : "uses"
EquipmentRouter --> Deps : "uses"
EventsRouter --> Deps : "uses"
ReportsRouter --> Deps : "uses"
SettingsRouter --> Deps : "uses"
```

**Diagram sources**
- [deps.py:75-188](file://app/api/deps.py#L75-L188)
- [auth.py:58-218](file://app/api/auth.py#L58-L218)
- [cameras.py:82-329](file://app/api/cameras.py#L82-L329)
- [employees.py:25-261](file://app/api/employees.py#L25-L261)
- [equipment.py:25-261](file://app/api/equipment.py#L25-L261)
- [events.py:40-191](file://app/api/events.py#L40-L191)
- [reports.py:43-177](file://app/api/reports.py#L43-L177)
- [settings.py:23-115](file://app/api/settings.py#L23-L115)

**Section sources**
- [deps.py:75-188](file://app/api/deps.py#L75-L188)

## Performance Considerations
- **MJPEG Streaming**: Live camera streams use a generator to yield JPEG frames at configurable intervals. Clients should handle connection drops gracefully and avoid excessive polling.
- **Frame Queue**: Processing FPS and frame queue size are configurable via environment variables to balance throughput and memory usage.
- **Evidence Retention**: Evidence files are automatically cleaned up based on retention days to prevent disk exhaustion.
- **Google Sheets Sync**: Background scheduler syncs events periodically with exponential retry logic to handle transient failures.
- **Rate Limiting**: No explicit rate limiting is implemented at the API level. Brute-force protection is applied to login attempts per username and client host.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- **CSRF Token Errors**: Ensure CSRF token is included in all POST requests. Tokens are generated per session and must be valid.
- **Authentication Failures**: Check session cookie `cctv_session` is present and not expired. In production, HTTPS is required for secure cookies.
- **Permission Denied**: Verify user roles (admin/operator/viewer) match endpoint requirements.
- **File Not Found**: Evidence or employee photos may have been deleted or expired. Check file paths and retention policies.
- **Model Loading Issues**: YOLO or face recognition models may be missing or incompatible. Use settings page to reload models and check warnings.
- **Database Locked**: Avoid multiple server instances sharing the same SQLite database. Use a single server instance or switch to PostgreSQL for concurrency.

**Section sources**
- [main.py:70-114](file://app/main.py#L70-L114)
- [README.md:516-530](file://README.md#L516-L530)

## Conclusion
The face-date system provides a comprehensive set of RESTful endpoints for managing cameras, employees, equipment, events, reports, and settings. Authentication and authorization are enforced through session cookies and CSRF tokens, with role-based access control ensuring appropriate permissions. The system supports live MJPEG streaming for camera feeds and secure file access for evidence retrieval. Configuration is environment-driven, allowing flexible deployment scenarios. Operators and administrators have distinct responsibilities, with clear separation of duties for sensitive operations.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Authentication Methods Summary
- **Session Cookies**: `cctv_session` cookie manages user sessions.
- **CSRF Protection**: All POST endpoints require `csrf_token` form field.
- **Roles**:
  - Admin: Full access to all endpoints.
  - Operator: Access to review, inventory operations, evidence access, and Sheets sync.
  - Viewer: Read-only access to dashboards, streams, journals, and reports.

### Versioning and Deprecation Policies
- **Versioning**: The application version is defined in the FastAPI metadata (`version="1.0.0"`). No URL-based versioning is currently implemented.
- **Deprecation**: No explicit deprecation endpoints or headers are used. Changes to API behavior should be communicated through documentation and release notes.

### Example API Calls Summary
- **Login**:
  ```bash
  curl -X POST http://localhost:8000/login \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "username=admin&password=Admin123!&next=/&csrf_token=TOKEN_FROM_SESSION"
  ```
- **Create Camera**:
  ```bash
  curl -X POST http://localhost:8000/api/cameras \
    -H "Content-Type: application/x-www-form-urlencoded" \
    -d "code=CAM-01&name=Entrance&source_type=rtsp&source=rtsp://admin:pass@192.168.1.64:554/stream&csrf_token=TOKEN_FROM_SESSION"
  ```
- **Stream Camera**:
  ```bash
  curl -N http://localhost:8000/api/cameras/CAM-01/stream
  ```
- **List Events**:
  ```bash
  curl "http://localhost:8000/api/events?limit=10&type=PERSON_ENTERED"
  ```
- **Export CSV**:
  ```bash
  curl -o events.csv "http://localhost:8000/reports/export.csv?start=2024-01-01&end=2024-12-31"
  ```

**Section sources**
- [main.py:44-53](file://app/main.py#L44-L53)
- [README.md:138-170](file://README.md#L138-L170)