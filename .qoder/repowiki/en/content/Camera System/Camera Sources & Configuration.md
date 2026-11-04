# Camera Sources & Configuration

<cite>
**Referenced Files in This Document**
- [camera.py](file://app/models/camera.py)
- [enums.py](file://app/models/enums.py)
- [camera_service.py](file://app/services/camera_service.py)
- [cameras.py](file://app/api/cameras.py)
- [monitoring_service.py](file://app/services/monitoring_service.py)
- [config.py](file://app/config.py)
- [test_camera.py](file://scripts/test_camera.py)
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
This document explains how camera sources are configured, validated, and consumed by the application. It covers all supported source types: RTSP streams, USB devices, file-based video sources, and simulation mode. It also documents the `Camera` model structure, source handling with credential masking, settings JSON configuration, URL patterns, environment-specific behavior, and practical setup guidance.

The system stores camera metadata in a database, runs one capture worker per enabled camera, decouples frame capture from inference through queues, and exposes live MJPEG streams and snapshots through FastAPI endpoints.

## Project Structure
Camera-related functionality is split across models, services, API routes, and configuration:

- Model layer defines the persistent camera record and shared enumerations.
- Service layer implements source readers, worker threads, and runtime monitoring.
- API layer provides CRUD for cameras, validation, and live streaming endpoints.
- Configuration layer loads environment variables and controls global processing behavior.

```mermaid
graph TB
subgraph "Model Layer"
CameraModel["Camera model<br/>source_type, source, settings"]
Enums["SourceType, CameraStatus"]
end
subgraph "Service Layer"
SourceReader["SourceReader interface"]
OpenCVSource["OpenCVSource<br/>RTSP / USB / File"]
SimulationSource["SimulationSource"]
CameraWorker["CameraWorker<br/>capture loop + reconnection"]
CameraMonitor["CameraMonitor<br/>lifecycle + status events"]
end
subgraph "API Layer"
CamerasRouter["Cameras router<br/>CRUD + stream + snapshot"]
end
subgraph "Configuration"
Settings["Settings<br/>SIMULATION_MODE, FPS, size, reconnect"]
end
CamerasRouter --> CameraMonitor
CameraMonitor --> CameraWorker
CameraWorker --> SourceReader
SourceReader --> OpenCVSource
SourceReader --> SimulationSource
CameraWorker --> Settings
CameraModel --> Enums
CameraWorker --> Enums
```

**Diagram sources**
- [camera.py:16-96](file://app/models/camera.py#L16-L96)
- [enums.py:151-175](file://app/models/enums.py#L151-L175)
- [camera_service.py:69-220](file://app/services/camera_service.py#L69-L220)
- [camera_service.py:226-448](file://app/services/camera_service.py#L226-L448)
- [monitoring_service.py:39-162](file://app/services/monitoring_service.py#L39-L162)
- [cameras.py:82-215](file://app/api/cameras.py#L82-L215)
- [config.py:62-99](file://app/config.py#L62-L99)

**Section sources**
- [camera.py:16-96](file://app/models/camera.py#L16-L96)
- [enums.py:151-175](file://app/models/enums.py#L151-L175)
- [camera_service.py:69-220](file://app/services/camera_service.py#L69-L220)
- [camera_service.py:226-448](file://app/services/camera_service.py#L226-L448)
- [monitoring_service.py:39-162](file://app/services/monitoring_service.py#L39-L162)
- [cameras.py:82-215](file://app/api/cameras.py#L82-L215)
- [config.py:62-99](file://app/config.py#L62-L99)

## Core Components
This section summarizes the key components involved in camera source configuration and runtime behavior.

- `Camera` model:
  - Stores `source_type`, `source`, `settings`, and runtime status fields.
  - Provides `masked_source` to hide credentials when displaying addresses.
  - Exposes `confidence_override` from settings JSON.
- `SourceType` enumeration:
  - Defines supported values: `rtsp`, `usb`, `file`, `simulation`.
- `CameraConfig`:
  - Immutable snapshot used by workers and pipelines.
  - Includes line geometry, flip-line flag, confidence override, and extra settings.
- `SourceReader` hierarchy:
  - `OpenCVSource` handles RTSP, USB, and file paths via OpenCV.
  - `SimulationSource` generates synthetic frames for testing.
- `CameraWorker`:
  - Owns a capture thread, manages reconnection backoff, publishes frames, and tracks health.
- `CameraMonitor`:
  - Starts/stops workers, maps DB rows to runtime objects, and records connection events.
- API routes:
  - Validate form inputs, persist camera records, start or stop workers, and expose live streams.

**Section sources**
- [camera.py:16-96](file://app/models/camera.py#L16-L96)
- [enums.py:171-175](file://app/models/enums.py#L171-L175)
- [camera_service.py:32-63](file://app/services/camera_service.py#L32-L63)
- [camera_service.py:69-220](file://app/services/camera_service.py#L69-L220)
- [camera_service.py:226-448](file://app/services/camera_service.py#L226-L448)
- [monitoring_service.py:39-162](file://app/services/monitoring_service.py#L39-L162)
- [cameras.py:50-79](file://app/api/cameras.py#L50-L79)
- [cameras.py:88-215](file://app/api/cameras.py#L88-L215)

## Architecture Overview
The camera pipeline connects configuration, capture, and runtime monitoring:

```mermaid
sequenceDiagram
participant Admin as "Admin UI"
participant API as "Cameras Router"
participant DB as "Database"
participant Monitor as "CameraMonitor"
participant Worker as "CameraWorker"
participant Source as "SourceReader"
Admin->>API : POST create/update camera
API->>DB : Persist Camera row
API->>Monitor : start_camera(CameraConfig)
Monitor->>Worker : construct + start()
Worker->>Source : build_source(config, settings)
Source-->>Worker : open()
Worker->>Source : read()
Source-->>Worker : frame or None
Worker->>Monitor : on_status_change(status, message)
Monitor->>DB : update status + timestamps
```

**Diagram sources**
- [cameras.py:88-215](file://app/api/cameras.py#L88-L215)
- [monitoring_service.py:128-162](file://app/services/monitoring_service.py#L128-L162)
- [camera_service.py:217-220](file://app/services/camera_service.py#L217-L220)
- [camera_service.py:254-284](file://app/services/camera_service.py#L254-L284)
- [camera_service.py:347-427](file://app/services/camera_service.py#L347-L427)

## Detailed Component Analysis

### Camera Model and Source Handling
The `Camera` model persists camera metadata and runtime state. Important aspects include:

- `source_type`: String field storing the selected source type; defaults to RTSP.
- `source`: Text field holding the raw address, device index, or file path. It may contain credentials and must never be returned directly by APIs.
- `settings`: JSON field storing per-camera options such as `flip_line` and `confidence`.
- `masked_source`: Property that masks embedded passwords in URLs before display.
- `confidence_override`: Reads a numeric confidence value from settings if present.

Credential masking logic:
- If the source contains a scheme and an `@` host separator, it replaces the password portion with `***`.
- If no scheme or no credentials are found, the original string is returned unchanged.

```mermaid
flowchart TD
Start(["masked_source access"]) --> CheckEmpty{"source empty?"}
CheckEmpty --> |Yes| ReturnEmpty["Return empty string"]
CheckEmpty --> |No| SplitScheme["Split scheme and rest"]
SplitScheme --> HasAt{"'@' present?"}
HasAt --> |No| ReturnRaw["Return original source"]
HasAt --> |Yes| SplitCreds["Split credentials and host"]
SplitCreds --> ExtractUser["Extract username"]
ExtractUser --> Masked["Return 'scheme://user:***@host'"]
```

**Diagram sources**
- [camera.py:72-84](file://app/models/camera.py#L72-L84)

**Section sources**
- [camera.py:16-96](file://app/models/camera.py#L16-L96)

### Supported Source Types and URL Patterns
Supported source types are defined by the `SourceType` enumeration:

- `rtsp`: Network camera streams.
- `usb`: Local USB camera device indexing.
- `file`: Video files stored on disk.
- `simulation`: Synthetic frames for offline testing.

RTSP URL patterns:
- The API accepts URLs starting with `rtsp://`, `rtsps://`, `http://`, or `https://`.
- Credentials can be embedded using standard URL userinfo syntax: `username:password@host`.
- The backend does not implement network discovery; it relies on configured URLs.

USB device indexing:
- The source string uses the prefix `usb:` followed by a numeric device index.
- The reader converts this into an integer passed to OpenCV.
- Invalid or missing digits default to device index `0`.

File-based video sources:
- The source string may optionally use the prefix `file:` followed by a path.
- The reader normalizes the path and passes it to OpenCV.

Simulation mode:
- When `SourceType.SIMULATION` is set, or when `SIMULATION_MODE` is enabled globally, the system uses `SimulationSource`.
- This produces deterministic frames with synthetic objects and ground-truth boxes for end-to-end testing without real hardware.

```mermaid
classDiagram
class SourceType {
+string value
}
class SourceReader {
+open() bool
+read() Frame?
+release() void
}
class OpenCVSource {
-string source_type
-object _source_value
+open() bool
+read() Frame?
+release() void
}
class SimulationSource {
+width int
+height int
+fps int
+loop_seconds float
+ground_truth list
+open() bool
+read() Frame?
+release() void
}
SourceReader <|-- OpenCVSource
SourceReader <|-- SimulationSource
```

**Diagram sources**
- [enums.py:171-175](file://app/models/enums.py#L171-L175)
- [camera_service.py:69-132](file://app/services/camera_service.py#L69-L132)
- [camera_service.py:134-215](file://app/services/camera_service.py#L134-L215)

**Section sources**
- [enums.py:171-175](file://app/models/enums.py#L171-L175)
- [cameras.py:64-71](file://app/api/cameras.py#L64-L71)
- [camera_service.py:82-98](file://app/services/camera_service.py#L82-L98)
- [camera_service.py:134-215](file://app/services/camera_service.py#L134-L215)

### Settings JSON Configuration
Per-camera settings are stored in the `settings` JSON column. The current implementation supports:

- `flip_line`: Boolean flag controlling whether the virtual entrance/exit line should be flipped.
- `confidence`: Optional numeric confidence override for detection.

These values are loaded into `CameraConfig.extra` and exposed through properties like `confidence_override`.

Example configuration concepts:
- Enabling line flipping: `{"flip_line": true}`
- Overriding detection confidence: `{"confidence": 0.6}`
- Combining both: `{"flip_line": true, "confidence": 0.6}`

**Section sources**
- [camera.py:36-36](file://app/models/camera.py#L36-L36)
- [camera.py:86-92](file://app/models/camera.py#L86-L92)
- [camera_service.py:48-63](file://app/services/camera_service.py#L48-L63)
- [cameras.py:123-137](file://app/api/cameras.py#L123-L137)
- [cameras.py:196-204](file://app/api/cameras.py#L196-L204)

### Runtime Behavior and Reconnection
Each enabled camera runs inside a `CameraWorker` thread:

- The worker opens the source via `build_source`, which selects `SimulationSource` under simulation conditions or `OpenCVSource` otherwise.
- Frames are read and pushed into a bounded queue; older frames are dropped when the queue is full.
- On read failures or open failures, the worker applies exponential backoff up to a maximum retry interval.
- Status transitions are reported to `CameraMonitor`, which updates the database and emits connection/disconnection events.

```mermaid
flowchart TD
Start(["Worker loop"]) --> OpenCheck{"Source opened?"}
OpenCheck --> |No| Backoff["Calculate backoff"]
Backoff --> SetOffline["Set OFFLINE status"]
SetOffline --> WaitBackoff["Wait backoff or stop signal"]
WaitBackoff --> OpenCheck
OpenCheck --> |Yes| ReadFrame["Read frame"]
ReadFrame --> FrameOK{"Frame received?"}
FrameOK --> |No| ReleaseAndFail["Release source and increment failures"]
ReleaseAndFail --> Backoff
FrameOK --> Resize["Resize to processing size"]
Resize --> Publish["Publish raw + annotated frame"]
Publish --> Sleep["Sleep until next cycle"]
Sleep --> Start
```

**Diagram sources**
- [camera_service.py:365-427](file://app/services/camera_service.py#L365-L427)
- [camera_service.py:347-363](file://app/services/camera_service.py#L347-L363)

**Section sources**
- [camera_service.py:217-220](file://app/services/camera_service.py#L217-L220)
- [camera_service.py:338-427](file://app/services/camera_service.py#L338-L427)
- [monitoring_service.py:171-216](file://app/services/monitoring_service.py#L171-L216)

### API Validation and Persistence
The cameras API validates and persists camera configurations:

- Code must be alphanumeric with allowed hyphens and underscores, and limited length.
- Name is required.
- Source is required except for simulation.
- For RTSP, the URL must start with accepted schemes.
- Line coordinates must either be fully provided or omitted entirely; start and end points cannot be identical.
- On creation or update, the API stops any existing worker, persists changes, and starts a new worker if enabled.

```mermaid
sequenceDiagram
participant Client as "Client"
participant Router as "Cameras Router"
participant Validator as "_validate_camera_form"
participant DB as "Database"
participant Monitor as "CameraMonitor"
Client->>Router : POST /api/cameras
Router->>Validator : Validate code, name, source_type, source, line
Validator-->>Router : Error or OK
Router->>DB : Create/Update Camera
Router->>Monitor : start_camera(CameraConfig)
Monitor-->>Router : Worker started
Router-->>Client : Redirect
```

**Diagram sources**
- [cameras.py:50-79](file://app/api/cameras.py#L50-L79)
- [cameras.py:88-154](file://app/api/cameras.py#L88-L154)
- [cameras.py:157-215](file://app/api/cameras.py#L157-L215)

**Section sources**
- [cameras.py:50-79](file://app/api/cameras.py#L50-L79)
- [cameras.py:88-215](file://app/api/cameras.py#L88-L215)

### Environment-Specific Setup and Global Simulation Mode
Global behavior is controlled by environment variables loaded into `Settings`:

- `SIMULATION_MODE`: Enables synthetic frames regardless of individual camera source type.
- `PROCESSING_FPS`, `PROCESSING_WIDTH`, `PROCESSING_HEIGHT`: Control frame rate and target resolution.
- `MAX_FRAME_QUEUE`: Controls the bounded queue size for frame buffering.
- `CAMERA_RECONNECT_INTERVAL`, `CAMERA_MAX_RETRY_INTERVAL`: Control reconnection timing and maximum backoff.

Environment loading:
- `.env` is loaded at import time if present.
- `get_settings()` caches the process-wide configuration.
- `reload_settings()` clears the cache and rebuilds settings.

Security considerations:
- Secrets such as `APP_SECRET_KEY` and bootstrap credentials are loaded from environment variables.
- Production checks warn about default secrets.

**Section sources**
- [config.py:18-20](file://app/config.py#L18-L20)
- [config.py:62-99](file://app/config.py#L62-L99)
- [config.py:129-156](file://app/config.py#L129-L156)
- [config.py:159-222](file://app/config.py#L159-L222)
- [config.py:225-243](file://app/config.py#L225-L243)

## Dependency Analysis
The following diagram shows how components depend on each other during camera lifecycle management:

```mermaid
graph LR
Config["config.Settings"] --> Monitor["monitoring_service.CameraMonitor"]
Config --> Worker["camera_service.CameraWorker"]
Enums["models.enums.SourceType"] --> Worker
Enums --> API["api.cameras"]
Model["models.camera.Camera"] --> API
Model --> Worker
API --> Monitor
Monitor --> Worker
Worker --> Source["camera_service.SourceReader"]
```

**Diagram sources**
- [config.py:62-99](file://app/config.py#L62-L99)
- [monitoring_service.py:39-162](file://app/services/monitoring_service.py#L39-L162)
- [camera_service.py:226-448](file://app/services/camera_service.py#L226-L448)
- [cameras.py:82-215](file://app/api/cameras.py#L82-L215)
- [camera.py:16-96](file://app/models/camera.py#L16-L96)
- [enums.py:171-175](file://app/models/enums.py#L171-L175)

**Section sources**
- [config.py:62-99](file://app/config.py#L62-L99)
- [monitoring_service.py:39-162](file://app/services/monitoring_service.py#L39-L162)
- [camera_service.py:226-448](file://app/services/camera_service.py#L226-L448)
- [cameras.py:82-215](file://app/api/cameras.py#L82-L215)
- [camera.py:16-96](file://app/models/camera.py#L16-L96)
- [enums.py:171-175](file://app/models/enums.py#L171-L175)

## Performance Considerations
- Frame buffering: A bounded queue prevents slow inference from stalling capture; excess frames are dropped intentionally.
- Resizing: Frames are resized to configured processing dimensions to reduce downstream compute cost.
- Reconnection backoff: Exponential backoff avoids tight retry loops while still recovering quickly after transient failures.
- Simulation mode: Useful for development and CI without requiring real cameras or heavy decoding.

Recommendations:
- Tune `PROCESSING_FPS`, `PROCESSING_WIDTH`, and `PROCESSING_HEIGHT` based on available CPU/GPU resources.
- Adjust `MAX_FRAME_QUEUE` to balance latency versus robustness against inference spikes.
- Use `SIMULATION_MODE` for quick integration tests before deploying real camera sources.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common issues and diagnostics:

- RTSP connection failures:
  - Verify the URL scheme and hostname.
  - Ensure credentials are correctly embedded in the URL.
  - Confirm the camera exposes a compatible stream (for example, H.264 substream).
  - Check firewall and antivirus rules blocking outbound connections.
- USB device indexing:
  - Ensure the correct device index is used (`usb:0`, `usb:1`, etc.).
  - On some systems, device indices may change between boots.
- File-based sources:
  - Confirm the file path exists and is readable.
  - Use the optional `file:` prefix if needed.
- Simulation mode:
  - Enable `SIMULATION_MODE` to bypass real hardware.
  - Use synthetic ground truth for end-to-end pipeline validation.

Operational checks:
- Inspect camera status messages and consecutive failure counts in the database.
- Review logs for “opening failed” or “read error” messages.
- Use the live stream and snapshot endpoints to verify frame delivery.

**Section sources**
- [camera_service.py:99-116](file://app/services/camera_service.py#L99-L116)
- [camera_service.py:347-427](file://app/services/camera_service.py#L347-L427)
- [monitoring_service.py:184-216](file://app/services/monitoring_service.py#L184-L216)
- [test_camera.py:125-132](file://scripts/test_camera.py#L125-L132)

## Conclusion
The camera subsystem provides flexible source support, secure credential handling, and resilient runtime behavior. By combining explicit source types, environment-driven simulation, and robust reconnection logic, it supports both production deployments and development workflows. Operators should configure RTSP URLs carefully, validate USB indices, ensure file paths are accessible, and leverage simulation mode for testing. Security best practices emphasize environment-based secret management and masked source exposure.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Practical Source Configuration Examples
Conceptual examples for different source types:

- RTSP with embedded credentials:
  - Pattern: `rtsp://username:password@camera-host:port/stream-path`
  - Accepted schemes: `rtsp://`, `rtsps://`, `http://`, `https://`
- USB device:
  - Pattern: `usb:0`
  - Index corresponds to the OS-assigned camera device number.
- File-based video:
  - Pattern: `file:/absolute/path/to/video.mp4`
  - Path normalization is handled by the reader.
- Simulation:
  - Set `source_type` to `simulation`, or enable `SIMULATION_MODE` globally.

**Section sources**
- [cameras.py:64-71](file://app/api/cameras.py#L64-L71)
- [camera_service.py:82-98](file://app/services/camera_service.py#L82-L98)
- [camera_service.py:134-215](file://app/services/camera_service.py#L134-L215)
- [config.py:206-206](file://app/config.py#L206-L206)

### Security Best Practices for Credential Management
- Store secrets in environment variables rather than hard-coding them.
- Avoid logging raw source strings that may contain passwords.
- Use HTTPS or RTSPS where supported by the camera.
- Restrict network access to trusted subnets.
- Rotate credentials regularly and audit access logs.

**Section sources**
- [config.py:140-156](file://app/config.py#L140-L156)
- [camera.py:24-26](file://app/models/camera.py#L24-L26)
- [camera.py:72-84](file://app/models/camera.py#L72-L84)