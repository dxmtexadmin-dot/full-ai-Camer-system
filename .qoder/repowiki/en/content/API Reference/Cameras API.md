# Cameras API

<cite>
**Referenced Files in This Document**
- [cameras.py](file://app/api/cameras.py)
- [camera_service.py](file://app/services/camera_service.py)
- [camera.py](file://app/models/camera.py)
- [enums.py](file://app/models/enums.py)
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

## Introduction
This document provides comprehensive API documentation for camera management in the system. It covers camera configuration, virtual line setup for movement detection, connection status monitoring, MJPEG streaming endpoints, validation rules, and error handling behavior.

The repository implements camera management through FastAPI routes, a camera service with worker threads, SQLAlchemy models, and shared enums. The live stream is delivered as an MJPEG multipart response, while runtime health and pipeline metrics are exposed through dedicated status endpoints.

## Project Structure
Camera-related functionality is primarily implemented in:
- `app/api/cameras.py`: HTTP endpoints for camera CRUD, toggling, deletion, streaming, snapshots, and status.
- `app/services/camera_service.py`: Camera capture, source abstraction, worker thread lifecycle, frame queueing, reconnection logic, and health reporting.
- `app/models/camera.py`: Database model for camera configuration, virtual line coordinates, settings, masking of credentials, and helper properties.
- `app/models/enums.py`: Shared enumerations for source types, camera statuses, event types, roles, and other domain values.
- `README.md`: User-facing camera guidance, example sources, operational notes, and troubleshooting tips.

```mermaid
graph TB
Client["Client"] --> API["FastAPI Router<br/>app/api/cameras.py"]
API --> DB["SQLAlchemy Session<br/>Cameras table"]
API --> Monitor["Monitoring Service<br/>Runtime state"]
Monitor --> Worker["CameraWorker<br/>app/services/camera_service.py"]
Worker --> Source["SourceReader<br/>RTSP / USB / File / Simulation"]
Source --> Capture["OpenCV / Simulation"]
API --> Stream["MJPEG Stream<br/>/api/cameras/{code}/stream"]
API --> Snapshot["Snapshot<br/>/api/cameras/{code}/snapshot"]
API --> Status["Status<br/>/api/cameras/{code}/status"]
```

**Diagram sources**
- [cameras.py:82-328](file://app/api/cameras.py#L82-L328)
- [camera_service.py:226-447](file://app/services/camera_service.py#L226-L447)
- [camera.py:16-96](file://app/models/camera.py#L16-L96)
- [enums.py:151-175](file://app/models/enums.py#L151-L175)

**Section sources**
- [cameras.py:1-329](file://app/api/cameras.py#L1-L329)
- [camera_service.py:1-448](file://app/services/camera_service.py#L1-L448)
- [camera.py:1-96](file://app/models/camera.py#L1-L96)
- [enums.py:1-183](file://app/models/enums.py#L1-L183)
- [README.md:202-242](file://README.md#L202-L242)

## Core Components
- Camera Model: Stores code, name, location, source type, source, enabled flag, virtual line coordinates, settings, status fields, timestamps, and helpers such as masked source and confidence override.
- CameraConfig: Immutable snapshot used by the worker to safely access configuration from background threads.
- SourceReader Abstraction: Defines open/read/release interface; concrete implementations include OpenCV-based RTSP/USB/File and deterministic Simulation source.
- CameraWorker: Owns capture thread, manages frame queue, publishes latest frames, handles reconnection with exponential backoff, and exposes health data.
- API Endpoints: Provide camera creation, update, toggle, delete, listing page, MJPEG stream, snapshot, and status.

Key responsibilities:
- Validation: Code format, length, required fields, source type constraints, RTSP URL prefix, virtual line coordinate consistency.
- Runtime Monitoring: Worker lifecycle, status transitions, frame counters, last frame timestamp, pipeline health.
- Streaming: MJPEG multipart response with placeholder frames when no annotated frame is available.
- Security: Admin-only mutation endpoints with CSRF verification; user-level read/stream endpoints.

**Section sources**
- [camera.py:16-96](file://app/models/camera.py#L16-L96)
- [camera_service.py:32-63](file://app/services/camera_service.py#L32-L63)
- [camera_service.py:69-220](file://app/services/camera_service.py#L69-L220)
- [camera_service.py:226-447](file://app/services/camera_service.py#L226-L447)
- [cameras.py:50-79](file://app/api/cameras.py#L50-L79)
- [cameras.py:88-154](file://app/api/cameras.py#L88-L154)
- [cameras.py:157-215](file://app/api/cameras.py#L157-L215)
- [cameras.py:218-269](file://app/api/cameras.py#L218-L269)
- [cameras.py:290-328](file://app/api/cameras.py#L290-L328)

## Architecture Overview
The camera subsystem combines web endpoints, database persistence, background workers, and streaming.

```mermaid
sequenceDiagram
participant Admin as "Admin Client"
participant API as "FastAPI /api/cameras"
participant DB as "Database"
participant Monitor as "Monitor"
participant Worker as "CameraWorker"
participant Source as "SourceReader"
Admin->>API : POST /api/cameras (create)
API->>DB : Insert Camera row
DB-->>API : Persisted camera
API->>Monitor : start_camera(CameraConfig)
Monitor->>Worker : start()
Worker->>Source : open()
Source-->>Worker : opened or failed
Worker->>Worker : _run loop, read frames
Note over Worker,Source : Reconnect with backoff on failure
```

**Diagram sources**
- [cameras.py:88-154](file://app/api/cameras.py#L88-L154)
- [camera_service.py:226-447](file://app/services/camera_service.py#L226-L447)

## Detailed Component Analysis

### Camera Listing Page
- Endpoint: GET `/cameras`
- Purpose: Renders the admin camera management page with merged runtime state.
- Behavior: Queries all cameras, merges with monitor runtime, includes running flag, status, message, frame counts, last frame time, and pipeline health.

**Section sources**
- [cameras.py:28-47](file://app/api/cameras.py#L28-L47)
- [cameras.py:82-85](file://app/api/cameras.py#L82-L85)

### Create Camera
- Endpoint: POST `/api/cameras`
- Authentication: Admin required; CSRF token validated.
- Form Fields:
  - `code`: Unique identifier; alphanumeric plus `-` and `_`, max 32 characters.
  - `name`: Human-readable name; required.
  - `location`: Optional location string.
  - `source_type`: One of `rtsp`, `usb`, `file`, `simulation`.
  - `source`: Required except for simulation; must be valid per source type.
  - `is_enabled`: Boolean flag to start immediately.
  - `line_x1`, `line_y1`, `line_x2`, `line_y2`: Virtual line coordinates; if any provided, all four must be present and not identical points.
  - `flip_line`: Optional boolean to reverse direction.
  - `confidence`: Optional per-camera confidence override.
  - `csrf_token`: CSRF protection.
- Validation Rules:
  - Code format and length enforced.
  - Name required.
  - Source type must be valid enum value.
  - Source required unless simulation.
  - RTSP source must start with allowed schemes.
  - Virtual line coordinates must be complete and non-degenerate.
- Persistence: Inserts camera row, records audit action, commits transaction.
- Runtime: If enabled, starts camera via monitor using `CameraConfig.from_model`.

Example payload structure (form-encoded):
- `code=CAM-01`
- `name=Entrance`
- `location=Lobby`
- `source_type=rtsp`
- `source=rtsp://admin:pass@192.168.1.64:554/stream`
- `is_enabled=true`
- `line_x1=100`, `line_y1=300`, `line_x2=700`, `line_y2=300`
- `flip_line=`
- `confidence=0.6`
- `csrf_token=<token>`

**Section sources**
- [cameras.py:88-154](file://app/api/cameras.py#L88-L154)
- [cameras.py:50-79](file://app/api/cameras.py#L50-L79)
- [camera_service.py:32-63](file://app/services/camera_service.py#L32-L63)

### Update Camera
- Endpoint: POST `/api/cameras/{camera_id}/update`
- Authentication: Admin required; CSRF token validated.
- Behavior:
  - Loads camera by ID; returns redirect with flash error if not found.
  - Validates form fields using same rules as create.
  - Stops existing camera worker before applying changes.
  - Updates name, location, source type, source, enabled flag, virtual line, settings, and timestamp.
  - Sets disabled status if disabled.
  - Records audit action and commits.
  - Restarts camera if enabled.

Note: There is no RESTful PUT endpoint defined; updates use this POST route.

**Section sources**
- [cameras.py:157-215](file://app/api/cameras.py#L157-L215)
- [cameras.py:50-79](file://app/api/cameras.py#L50-L79)

### Toggle Camera
- Endpoint: POST `/api/cameras/{camera_id}/toggle`
- Authentication: Admin required; CSRF token validated.
- Behavior:
  - Flips `is_enabled`.
  - Sets status to unknown when enabling, disabled when disabling.
  - Starts or stops camera worker accordingly.
  - Records audit action and commits.

**Section sources**
- [cameras.py:218-246](file://app/api/cameras.py#L218-L246)

### Delete Camera
- Endpoint: POST `/api/cameras/{camera_id}/delete`
- Authentication: Admin required; CSRF token validated.
- Behavior:
  - Stops camera worker.
  - Deletes camera row.
  - Records audit action and commits.

Note: There is no RESTful DELETE endpoint defined; deletion uses this POST route.

**Section sources**
- [cameras.py:249-269](file://app/api/cameras.py#L249-L269)

### MJPEG Live Stream
- Endpoint: GET `/api/cameras/{code}/stream`
- Authentication: Any authenticated user.
- Response: Multipart JPEG stream (`multipart/x-mixed-replace`).
- Behavior:
  - Streams latest annotated JPEG frames at approximately 8 FPS.
  - Uses placeholder frame when no annotated frame is available.
  - Disables caching headers to ensure fresh frames.

Usage example:
- `GET http://127.0.0.1:8000/api/cameras/CAM-01/stream`

**Section sources**
- [cameras.py:275-298](file://app/api/cameras.py#L275-L298)

### Snapshot
- Endpoint: GET `/api/cameras/{code}/snapshot`
- Authentication: Any authenticated user.
- Response: Single JPEG image.
- Behavior:
  - Returns latest annotated JPEG if available.
  - Otherwise returns placeholder image and flashes an error message.

Usage example:
- `GET http://127.0.0.1:8000/api/cameras/CAM-01/snapshot`

**Section sources**
- [cameras.py:301-309](file://app/api/cameras.py#L301-L309)

### Connection Status Monitoring
- Endpoint: GET `/api/cameras/{code}/status`
- Authentication: Any authenticated user.
- Response: JSON object containing:
  - `code`: Camera code.
  - `running`: Whether worker thread is alive.
  - `status`: Enum value from `CameraStatus`.
  - `message`: Human-readable status message.
  - `frames_captured`: Total frames captured.
  - `frames_dropped`: Dropped frames due to queue overflow.
  - `last_frame_at`: Localized timestamp of last frame.
  - `status_label`: Human-readable label for status.
  - Pipeline health fields if pipeline exists.

Behavior:
- If runtime is missing, returns disabled status with `running=false`.
- Otherwise aggregates worker health and optional pipeline health.

**Section sources**
- [cameras.py:312-328](file://app/api/cameras.py#L312-L328)
- [enums.py:151-168](file://app/models/enums.py#L151-L168)

### Camera Source Types and Configuration Parameters
Supported source types:
- `rtsp`: RTSP URL; must start with allowed schemes.
- `usb`: Device index; numeric device number.
- `file`: Video file path; supports optional `file:` prefix.
- `simulation`: Synthetic scene for testing without real hardware.

Configuration parameters:
- `source_type`: Enum value determining source implementation.
- `source`: URL, device index, or file path depending on type.
- `line_x1`, `line_y1`, `line_x2`, `line_y2`: Virtual line coordinates in pixels relative to processing resolution.
- `flip_line`: Reverse direction detection across the line.
- `confidence`: Per-camera confidence override for detection thresholds.

Implementation details:
- `OpenCVSource` handles RTSP/USB/file via `cv2.VideoCapture`.
- `SimulationSource` generates synthetic frames with moving person and occasional laptop box.
- `build_source` selects simulation mode based on config or global settings.

**Section sources**
- [camera_service.py:82-132](file://app/services/camera_service.py#L82-L132)
- [camera_service.py:134-220](file://app/services/camera_service.py#L134-L220)
- [enums.py:171-175](file://app/models/enums.py#L171-L175)
- [README.md:217-227](file://README.md#L217-L227)

### Virtual Line Setup for Movement Detection
Virtual line defines a boundary across which movement is detected. Coordinates are in pixel space matching the processing resolution.

Rules:
- All four coordinates must be provided together.
- Start and end points must not be identical.
- Line should be perpendicular to walking direction and placed just inside the door area.
- Calibration depends on `PROCESSING_WIDTH` and `PROCESSING_HEIGHT`.

Operational guidance:
- Use test script to capture frames and determine pixel dimensions.
- Adjust line after camera angle or height changes.

**Section sources**
- [cameras.py:72-79](file://app/api/cameras.py#L72-L79)
- [camera.py:30-58](file://app/models/camera.py#L30-L58)
- [README.md:229-237](file://README.md#L229-L237)

### Error Handling and Network Issues
- Validation errors: Redirects to camera page with flash messages indicating invalid input.
- Missing camera: Flash error and redirect when updating/toggling/deleting nonexistent camera.
- Source open failures: Worker sets status to offline with retry backoff; logs errors.
- Frame read failures: Releases source, increments failure counter, applies exponential backoff.
- Streaming fallback: Placeholder frame shown when no annotated frame available.
- Status endpoint: Returns disabled state when runtime is absent.

Reconnection strategy:
- Exponential backoff capped by configured maximum interval.
- Backoff formula uses base reconnect interval multiplied by powers of two, limited to a maximum depth.

**Section sources**
- [cameras.py:116-118](file://app/api/cameras.py#L116-L118)
- [cameras.py:177-191](file://app/api/cameras.py#L177-L191)
- [cameras.py:227-230](file://app/api/cameras.py#L227-L230)
- [cameras.py:258-261](file://app/api/cameras.py#L258-L261)
- [camera_service.py:347-411](file://app/services/camera_service.py#L347-L411)
- [cameras.py:301-309](file://app/api/cameras.py#L301-L309)
- [cameras.py:312-328](file://app/api/cameras.py#L312-L328)

### API Summary Table
| Method | Path | Description | Auth | Notes |
|--------|------|-------------|------|-------|
| GET | `/cameras` | Render camera management page | Admin | HTML response |
| POST | `/api/cameras` | Create camera | Admin + CSRF | Form-encoded payload |
| POST | `/api/cameras/{camera_id}/update` | Update camera | Admin + CSRF | Form-encoded payload |
| POST | `/api/cameras/{camera_id}/toggle` | Enable/disable camera | Admin + CSRF | Toggles runtime worker |
| POST | `/api/cameras/{camera_id}/delete` | Remove camera | Admin + CSRF | Stops worker and deletes row |
| GET | `/api/cameras/{code}/stream` | MJPEG live stream | User | Multipart JPEG stream |
| GET | `/api/cameras/{code}/snapshot` | Latest snapshot | User | JPEG image |
| GET | `/api/cameras/{code}/status` | Connection and health status | User | JSON response |

**Section sources**
- [cameras.py:82-328](file://app/api/cameras.py#L82-L328)

## Dependency Analysis
The camera module depends on:
- FastAPI router and dependencies for request handling, authentication, CSRF verification, and session/database injection.
- SQLAlchemy session for database operations.
- Monitoring service for runtime state and worker lifecycle.
- Camera service for capture, inference decoupling, frame queueing, and health reporting.
- Enums for consistent status and source type values.
- Image utilities for JPEG encoding and overlay placeholders.
- Time utilities for localized timestamps.

```mermaid
classDiagram
class Camera {
+int id
+string code
+string name
+string location
+string source_type
+string source
+bool is_enabled
+int line_x1
+int line_y1
+int line_x2
+int line_y2
+dict settings
+string status
+string status_message
+datetime last_frame_at
+datetime last_connected_at
+int consecutive_failures
+has_line() bool
+line() tuple
+status_enum() CameraStatus
+masked_source() string
+confidence_override() float
}
class CameraConfig {
+int id
+string code
+string name
+string location
+string source_type
+string source
+bool is_enabled
+tuple line
+bool flip_line
+float confidence
+dict extra
+from_model(camera) CameraConfig
}
class SourceReader {
<<interface>>
+open() bool
+read() ndarray
+release() void
}
class OpenCVSource {
+open() bool
+read() ndarray
+release() void
}
class SimulationSource {
+open() bool
+read() ndarray
+release() void
+ground_truth list
}
class CameraWorker {
+start() void
+stop(timeout) void
+is_running bool
+latest_frame() ndarray
+latest_annotated_jpeg() bytes
+publish_annotated(frame) void
+health() dict
}
CameraConfig --> Camera : "from_model"
CameraWorker --> SourceReader : "uses"
OpenCVSource ..|> SourceReader
SimulationSource ..|> SourceReader
```

**Diagram sources**
- [camera.py:16-96](file://app/models/camera.py#L16-L96)
- [camera_service.py:32-63](file://app/services/camera_service.py#L32-L63)
- [camera_service.py:69-220](file://app/services/camera_service.py#L69-L220)
- [camera_service.py:226-447](file://app/services/camera_service.py#L226-L447)

**Section sources**
- [camera.py:16-96](file://app/models/camera.py#L16-L96)
- [camera_service.py:32-447](file://app/services/camera_service.py#L32-L447)

## Performance Considerations
- Frame Queue: Bounded queue drops oldest frames when full to avoid blocking capture; dropped frames are counted.
- Decoupled Processing: Capture and inference are separated so slow models do not stall network reads.
- Resize Strategy: Frames are resized to configured processing width/height using area interpolation when larger than target.
- MJPEG FPS: Stream defaults to 8 FPS; clients can adjust expectations accordingly.
- Reconnection Backoff: Exponential backoff prevents rapid retries under persistent network issues.
- Settings Impact: `PROCESSING_FPS`, `PROCESSING_WIDTH`, `PROCESSING_HEIGHT`, `CAMERA_RECONNECT_INTERVAL`, and `CAMERA_MAX_RETRY_INTERVAL` influence performance and stability.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and resolutions:
- Camera OFFLINE: Invalid RTSP URL, wrong credentials, network connectivity, codec mismatch. Use test script to validate source and H.264 sub-stream.
- Repeated connection failures: Source unavailable; apply backoff and correct source configuration.
- Port conflicts: Change server port or stop conflicting process.
- Database locked: Ensure single server instance; CLI scripts use short transactions.
- Low FPS or high CPU: Lower processing FPS, reduce image size, increase face recognition interval, or enable GPU.
- Unknown errors: Check application logs for tracebacks.

Operational tips:
- Use `scripts/test_camera.py` to verify source, frame dimensions, and FPS without starting the full pipeline.
- Calibrate virtual line coordinates using captured frames aligned with processing resolution.
- Monitor status endpoint for runtime diagnostics and pipeline health.

**Section sources**
- [README.md:516-530](file://README.md#L516-L530)
- [README.md:459-470](file://README.md#L459-L470)
- [cameras.py:312-328](file://app/api/cameras.py#L312-L328)

## Conclusion
The Cameras API provides robust camera management capabilities including creation, updates, toggling, deletion, live MJPEG streaming, snapshots, and detailed status monitoring. Validation ensures safe configuration inputs, while the worker-based architecture offers resilient reconnection and performance tuning options. For reliable operation, calibrate virtual lines according to processing resolution, configure appropriate FPS and image sizes, and monitor status endpoints for early detection of network or source issues.

[No sources needed since this section summarizes without analyzing specific files]