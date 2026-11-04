# Camera System

<cite>
**Referenced Files in This Document**
- [camera.py](file://app/models/camera.py)
- [enums.py](file://app/models/enums.py)
- [camera_service.py](file://app/services/camera_service.py)
- [monitoring_service.py](file://app/services/monitoring_service.py)
- [cameras.py](file://app/api/cameras.py)
- [config.py](file://app/config.py)
- [test_camera.py](file://scripts/test_camera.py)
- [test_camera.py](file://tests/test_camera.py)
- [tracking_service.py](file://app/services/tracking_service.py)
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
This document explains the camera system component: how cameras are configured, how frames are captured from RTSP, USB, file, and simulation sources, how virtual lines are defined and used for crossing detection, how connection health is monitored with exponential backoff reconnection, and how MJPEG live streaming and snapshot retrieval work through the API. It also provides practical configuration examples, calibration guidance using `scripts/test_camera.py`, and best practices for camera placement and line positioning.

## Project Structure
The camera subsystem spans models, services, API endpoints, configuration, and test utilities:

```mermaid
graph TB
subgraph "Models"
M_Camera["Camera model<br/>configuration + status"]
M_Enums["Source types + statuses"]
end
subgraph "Services"
S_Cap["Camera capture worker<br/>frame loop + backoff"]
S_Monitor["Monitoring service<br/>status transitions + events"]
S_Track["Tracking service<br/>line crossing logic"]
end
subgraph "API"
A_Router["FastAPI router<br/>CRUD + stream + snapshot"]
end
subgraph "Config"
C_Settings["Settings<br/>reconnect intervals + processing size"]
end
subgraph "Scripts & Tests"
T_CLI["CLI test script"]
T_Unit["Unit tests"]
end
M_Camera --> S_Cap
M_Enums --> S_Cap
C_Settings --> S_Cap
S_Cap --> S_Monitor
S_Monitor --> A_Router
S_Track --> A_Router
T_CLI --> S_Cap
T_Unit --> S_Cap
```

**Diagram sources**
- [camera.py:16-45](file://app/models/camera.py#L16-L45)
- [enums.py:151-175](file://app/models/enums.py#L151-L175)
- [camera_service.py:32-47](file://app/services/camera_service.py#L32-L47)
- [camera_service.py:226-251](file://app/services/camera_service.py#L226-L251)
- [monitoring_service.py:171-214](file://app/services/monitoring_service.py#L171-L214)
- [cameras.py:275-328](file://app/api/cameras.py#L275-L328)
- [config.py:92-98](file://app/config.py#L92-L98)
- [test_camera.py:58-80](file://scripts/test_camera.py#L58-L80)
- [test_camera.py:47-73](file://tests/test_camera.py#L47-L73)

**Section sources**
- [camera.py:16-45](file://app/models/camera.py#L16-L45)
- [enums.py:151-175](file://app/models/enums.py#L151-L175)
- [camera_service.py:32-47](file://app/services/camera_service.py#L32-L47)
- [camera_service.py:226-251](file://app/services/camera_service.py#L226-L251)
- [monitoring_service.py:171-214](file://app/services/monitoring_service.py#L171-L214)
- [cameras.py:275-328](file://app/api/cameras.py#L275-L328)
- [config.py:92-98](file://app/config.py#L92-L98)
- [test_camera.py:58-80](file://scripts/test_camera.py#L58-L80)
- [test_camera.py:47-73](file://tests/test_camera.py#L47-L73)

## Core Components
- **Camera model**: Stores camera identity, source type, masked source value, virtual line coordinates, per-camera settings, runtime status fields, timestamps, and failure counters.
- **Source abstraction**: Unified interface for frame producers; concrete implementations for OpenCV-backed RTSP/USB/file and a deterministic simulation source.
- **CameraWorker**: One-thread-per-camera capture loop that opens sources, reads frames, resizes to configured processing dimensions, publishes raw and annotated frames, and manages reconnection with exponential backoff.
- **MonitoringService**: Owns workers, persists status changes, and emits automatic `CAMERA_DISCONNECTED` / `CAMERA_RECONNECTED` events on state transitions.
- **API layer**: Provides camera CRUD, live MJPEG streaming, snapshot retrieval, and status queries.
- **Configuration**: Environment-driven settings controlling reconnect intervals, processing FPS/resolution, queue size, and simulation mode.
- **Line crossing logic**: Uses signed distance relative to the virtual line, supports hysteresis, cooldown, and direction flipping.

**Section sources**
- [camera.py:16-95](file://app/models/camera.py#L16-L95)
- [camera_service.py:69-220](file://app/services/camera_service.py#L69-L220)
- [camera_service.py:226-447](file://app/services/camera_service.py#L226-L447)
- [monitoring_service.py:171-214](file://app/services/monitoring_service.py#L171-L214)
- [cameras.py:275-328](file://app/api/cameras.py#L275-L328)
- [config.py:92-98](file://app/config.py#L92-L98)
- [tracking_service.py:210-237](file://app/services/tracking_service.py#L210-L237)

## Architecture Overview
The runtime architecture decouples capture from inference and UI:

```mermaid
sequenceDiagram
participant Admin as "Admin UI"
participant API as "FastAPI Router"
participant Monitor as "MonitoringService"
participant Worker as "CameraWorker"
participant Source as "OpenCVSource/SimulationSource"
participant DB as "Database"
Admin->>API : POST /api/cameras (create/update)
API->>DB : Persist Camera row
API->>Monitor : start_camera(CameraConfig)
Monitor->>Worker : start()
Worker->>Source : open()
Source-->>Worker : opened or failed
Worker->>Monitor : on_status_change(CONNECTING/ONLINE/OFFLINE)
Monitor->>DB : update status + last_connected_at
Note over Worker,Source : Read frames at processing_fps<br/>resize to processing_width x processing_height
API->>Monitor : GET /api/cameras/{code}/stream
Monitor->>Worker : latest_annotated_jpeg()
Worker-->>API : JPEG bytes
API-->>Admin : multipart/x-mixed-replace stream
```

**Diagram sources**
- [cameras.py:88-154](file://app/api/cameras.py#L88-L154)
- [camera_service.py:226-363](file://app/services/camera_service.py#L226-L363)
- [monitoring_service.py:171-214](file://app/services/monitoring_service.py#L171-L214)

## Detailed Component Analysis

### Camera Model and Virtual Line
- The camera model stores:
  - Identity and location.
  - Source type and source string (RTSP URL, USB device index, or file path).
  - Virtual line coordinates `(line_x1, line_y1, line_x2, line_y2)` in pixel coordinates.
  - Per-camera settings including `flip_line` and optional confidence override.
  - Runtime status, message, timestamps, and consecutive failure counter.
- Helper properties expose:
  - Whether a complete line is configured.
  - A normalized tuple-of-tuples representation of the line.
  - Masked source display (hides credentials).
  - Confidence override parsing.

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
+masked_source() string
+confidence_override() float?
}
```

**Diagram sources**
- [camera.py:16-95](file://app/models/camera.py#L16-L95)

**Section sources**
- [camera.py:16-95](file://app/models/camera.py#L16-L95)

### Source Configuration and Setup
Supported source types:
- **RTSP**: Use an RTSP URL. The API validates that RTSP sources start with supported schemes.
- **USB**: Use the prefix `usb:` followed by a numeric device index. Internally parsed to an integer device ID.
- **File**: Use a video file path. The implementation accepts an optional `file:` prefix and normalizes it to a path.
- **Simulation**: Deterministic synthetic scene with moving person and occasional laptop object; useful for offline testing.

Source construction rules:
- Simulation source is selected when the camera’s source type is simulation or when global simulation mode is enabled.
- Otherwise, an OpenCV-based reader is used for RTSP/USB/file.

Practical examples:
- RTSP URL formats:
  - `rtsp://user:password@192.168.1.64:554/Streaming/Channels/101`
  - `rtsps://...` for secure RTSP
  - HTTP-based streams are accepted by validation but typically use RTSP.
- USB device addressing:
  - `usb:0`, `usb:1`, etc., where the number is the OpenCV device index.
- File path configuration:
  - Absolute paths or paths prefixed with `file:`.
- Simulation mode usage:
  - Set source type to simulation or enable global simulation mode via settings.

**Section sources**
- [camera_service.py:82-132](file://app/services/camera_service.py#L82-L132)
- [camera_service.py:134-220](file://app/services/camera_service.py#L134-L220)
- [cameras.py:64-79](file://app/api/cameras.py#L64-L79)
- [test_camera.py:58-80](file://scripts/test_camera.py#L58-L80)

### Connection Monitoring and Exponential Backoff Reconnection
The worker maintains a bounded frame queue and a single capture thread. Its lifecycle:
- Starts a daemon thread per camera.
- Attempts to open the source; if unsuccessful, sets status to OFFLINE and schedules retry.
- On each read failure, releases the source and retries after a backoff interval.
- On successful read, sets status to ONLINE and resets failure counters.
- Exponential backoff formula:
  - Interval grows as `base_interval * 2^(min(failures - 1, 5))`.
  - Capped by a maximum retry interval.
- Status transitions:
  - CONNECTING → ONLINE on success.
  - CONNECTING → OFFLINE on initial open failure.
  - ONLINE → OFFLINE on read failure.
  - DISABLED when explicitly stopped/disabled.

```mermaid
flowchart TD
Start(["Start worker"]) --> Open["Open source"]
Open --> OpenOK{"Opened?"}
OpenOK --> |No| FailInc["failures += 1"]
FailInc --> Backoff["backoff = min(max_retry,<br/>base * 2^(min(failures-1,5)))"]
Backoff --> Offline["Set status OFFLINE"]
Offline --> Wait["Wait backoff or stop"]
Wait --> Open
OpenOK --> |Yes| Online["Set status ONLINE"]
Online --> Loop["Read frame"]
Loop --> ReadOK{"Frame received?"}
ReadOK --> |No| FailInc
ReadOK --> |Yes| Resize["Resize to processing size"]
Resize --> Publish["Publish raw + push queue"]
Publish --> Loop
```

**Diagram sources**
- [camera_service.py:347-427](file://app/services/camera_service.py#L347-L427)

**Section sources**
- [camera_service.py:347-427](file://app/services/camera_service.py#L347-L427)
- [config.py:200-205](file://app/config.py#L200-L205)

### MJPEG Live Streaming and Snapshot Retrieval
- **Live stream endpoint**: `/api/cameras/{code}/stream`
  - Streams annotated JPEG frames as `multipart/x-mixed-replace`.
  - If no frame is available yet, returns a placeholder image.
  - Frame rate is controlled by a small sleep interval.
- **Snapshot endpoint**: `/api/cameras/{code}/snapshot`
  - Returns the latest annotated JPEG or a placeholder if none exists.
- **Status endpoint**: `/api/cameras/{code}/status`
  - Returns worker health, status label, last frame timestamp, and pipeline metrics when available.

```mermaid
sequenceDiagram
participant Client as "Client"
participant API as "FastAPI Router"
participant Monitor as "MonitoringService"
participant Worker as "CameraWorker"
Client->>API : GET /api/cameras/{code}/stream
API->>Monitor : get_worker(code)
Monitor-->>API : worker
loop For each frame
API->>Worker : latest_annotated_jpeg()
alt Frame available
Worker-->>API : JPEG bytes
API-->>Client : --frame ... JPEG
else No frame
API-->>Client : --frame ... placeholder JPEG
end
end
Client->>API : GET /api/cameras/{code}/snapshot
API->>Worker : latest_annotated_jpeg()
Worker-->>API : JPEG or None
API-->>Client : image/jpeg
```

**Diagram sources**
- [cameras.py:275-328](file://app/api/cameras.py#L275-L328)

**Section sources**
- [cameras.py:275-328](file://app/api/cameras.py#L275-L328)

### Virtual Line Configuration and Calibration
Virtual line definition:
- Coordinates are stored as four integers representing two points in pixel space: `(line_x1, line_y1)` and `(line_x2, line_y2)`.
- The line must be fully specified; partial values are rejected by form validation.
- Direction semantics:
  - Crossing from negative to positive side is treated as IN; positive to negative as OUT.
  - When `flip_line` is enabled, directions are reversed.
- Placement guidance:
  - Position the line perpendicular to the expected movement direction across the entrance/exit.
  - Place the line where people cross consistently and avoid occlusions.
  - Use the UI’s coordinate inputs to fine-tune X/Y positions based on saved debug frames.

Calibration procedure using `scripts/test_camera.py`:
- Run the CLI against a specific camera code or directly provide source/type parameters.
- Capture frames to a debug directory and inspect them to determine accurate line coordinates.
- Save representative frames and adjust line coordinates until crossings match expected behavior.

```mermaid
flowchart TD
A["Run test_camera.py<br/>with --code or --source/--type"] --> B["Connect source and drain frames"]
B --> C["Save sample frames to output folder"]
C --> D["Inspect frame shape and content"]
D --> E["Adjust line_x1,line_y1,line_x2,line_y2 in UI"]
E --> F["Enable flip_line if direction is reversed"]
F --> G["Validate crossings with real traffic"]
```

**Diagram sources**
- [test_camera.py:58-133](file://scripts/test_camera.py#L58-L133)
- [cameras.py:50-79](file://app/api/cameras.py#L50-L79)

**Section sources**
- [camera.py:30-58](file://app/models/camera.py#L30-L58)
- [cameras.py:50-79](file://app/api/cameras.py#L50-L79)
- [tracking_service.py:210-237](file://app/services/tracking_service.py#L210-L237)
- [test_camera.py:58-133](file://scripts/test_camera.py#L58-L133)

### Automatic Disconnection/Reconnection Events
The monitoring service translates worker status changes into persistent database updates and event records:
- On transition to OFFLINE (from non-OFFLINE), emit `CAMERA_DISCONNECTED`.
- On transition to ONLINE (from OFFLINE, ERROR, or CONNECTING), emit `CAMERA_RECONNECTED`.
- Update `last_connected_at` and reset `consecutive_failures` when ONLINE.
- Increment `consecutive_failures` for OFFLINE/ERROR states.

```mermaid
stateDiagram-v2
[*] --> UNKNOWN
UNKNOWN --> CONNECTING : "start/open"
CONNECTING --> ONLINE : "open success"
CONNECTING --> OFFLINE : "open failure"
ONLINE --> OFFLINE : "read failure"
OFFLINE --> CONNECTING : "retry"
ONLINE --> DISABLED : "stop/disable"
OFFLINE --> DISABLED : "stop/disable"
```

**Diagram sources**
- [camera_service.py:347-427](file://app/services/camera_service.py#L347-L427)
- [monitoring_service.py:171-214](file://app/services/monitoring_service.py#L171-L214)
- [enums.py:151-168](file://app/models/enums.py#L151-L168)

**Section sources**
- [monitoring_service.py:171-214](file://app/services/monitoring_service.py#L171-L214)
- [camera_service.py:347-427](file://app/services/camera_service.py#L347-L427)

## Dependency Analysis
Key dependencies and relationships:
- **Camera model** depends on enums for status and source types.
- **CameraWorker** depends on:
  - Settings for processing dimensions, FPS, queue size, and reconnect intervals.
  - SourceReader implementations for frame acquisition.
  - Image encoding utilities for JPEG snapshots.
- **MonitoringService** depends on:
  - Database session scope to persist status and events.
  - Event creation service to record disconnection/reconnection events.
- **API router** depends on:
  - MonitoringService for runtime access.
  - Overlay utilities for placeholder frames.
  - Time utilities for formatting timestamps.

```mermaid
graph LR
Enums["enums.py"] --> Model["models/camera.py"]
Config["config.py"] --> Service["services/camera_service.py"]
Model --> Service
Service --> Monitor["services/monitoring_service.py"]
Monitor --> API["api/cameras.py"]
Service --> API
```

**Diagram sources**
- [enums.py:151-175](file://app/models/enums.py#L151-L175)
- [camera.py:16-45](file://app/models/camera.py#L16-L45)
- [config.py:92-98](file://app/config.py#L92-L98)
- [camera_service.py:32-47](file://app/services/camera_service.py#L32-L47)
- [monitoring_service.py:171-214](file://app/services/monitoring_service.py#L171-L214)
- [cameras.py:275-328](file://app/api/cameras.py#L275-L328)

**Section sources**
- [enums.py:151-175](file://app/models/enums.py#L151-L175)
- [camera.py:16-45](file://app/models/camera.py#L16-L45)
- [config.py:92-98](file://app/config.py#L92-L98)
- [camera_service.py:32-47](file://app/services/camera_service.py#L32-L47)
- [monitoring_service.py:171-214](file://app/services/monitoring_service.py#L171-L214)
- [cameras.py:275-328](file://app/api/cameras.py#L275-L328)

## Performance Considerations
- **Frame buffering**: The worker uses a bounded queue; when full, the oldest frame is dropped to keep latency low.
- **Backpressure**: Processing backlog is considered worse than skipping frames; the worker prioritizes fresh frames.
- **Resizing**: Frames are resized to configured processing width/height to reduce downstream compute cost.
- **JPEG quality**: Snapshots and placeholders are encoded with moderate JPEG quality to balance bandwidth and clarity.
- **Processing FPS**: Controlled by settings; higher FPS increases CPU/GPU load and network throughput.
- **RTSP backend tuning**: FFmpeg capture options can be tuned via environment variables to optimize latency and buffer behavior.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide
Common connectivity issues and resolutions:
- **RTSP URL problems**:
  - Verify scheme (`rtsp://` or `rtsps://`) and host accessibility.
  - Ensure credentials are correct and the camera exposes a compatible H.264 substream (e.g., channel 102).
  - Check firewall and antivirus blocks.
- **USB device indexing**:
  - Confirm the correct device index (`usb:0`, `usb:1`, etc.).
  - Validate OS-level device availability.
- **File path issues**:
  - Ensure the file exists and is readable.
  - Use absolute paths or the `file:` prefix.
- **Connection failures**:
  - Observe worker status transitioning to OFFLINE and check the status message for attempt count and next retry interval.
  - Review logs for backend-specific errors during open/read.
- **No frames captured**:
  - Use `scripts/test_camera.py` to isolate source connectivity and capture sample frames.
  - Inspect average FPS and frame counts reported by the CLI.

Operational checks:
- Use `/api/cameras/{code}/status` to inspect current status, messages, frame counters, and last frame timestamp.
- Use `/api/cameras/{code}/stream` to verify live MJPEG output.
- Use `/api/cameras/{code}/snapshot` to retrieve the latest annotated frame.

**Section sources**
- [test_camera.py:125-133](file://scripts/test_camera.py#L125-L133)
- [camera_service.py:347-427](file://app/services/camera_service.py#L347-L427)
- [cameras.py:290-328](file://app/api/cameras.py#L290-L328)

## Conclusion
The camera system provides robust multi-source capture, resilient reconnection with exponential backoff, clear status monitoring, and convenient APIs for live streaming and snapshots. Virtual lines enable reliable crossing detection with configurable direction flipping. Using the provided CLI tool and API endpoints, operators can calibrate lines, validate connectivity, and troubleshoot issues effectively. Proper configuration of source types, processing parameters, and line placement ensures accurate and performant operation.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### API Endpoints Summary
- **Create camera**: `POST /api/cameras`
- **Update camera**: `POST /api/cameras/{camera_id}/update`
- **Toggle camera**: `POST /api/cameras/{camera_id}/toggle`
- **Delete camera**: `POST /api/cameras/{camera_id}/delete`
- **Live stream**: `GET /api/cameras/{code}/stream`
- **Snapshot**: `GET /api/cameras/{code}/snapshot`
- **Status**: `GET /api/cameras/{code}/status`

**Section sources**
- [cameras.py:88-269](file://app/api/cameras.py#L88-L269)
- [cameras.py:290-328](file://app/api/cameras.py#L290-L328)

### Practical Configuration Examples
- RTSP URL:
  - `rtsp://user:password@192.168.1.64:554/Streaming/Channels/101`
- USB device:
  - `usb:0`
- File path:
  - `C:\video\kirish.mp4` or `file:C:\video\kirish.mp4`
- Simulation:
  - Source type set to simulation; optionally enable global simulation mode via settings.

**Section sources**
- [test_camera.py:8-14](file://scripts/test_camera.py#L8-L14)
- [camera_service.py:82-132](file://app/services/camera_service.py#L82-L132)
- [config.py:200-206](file://app/config.py#L200-L206)

### Best Practices for Camera Placement and Line Positioning
- Mount the camera at eye level or slightly above to minimize occlusion.
- Ensure consistent lighting and avoid glare on faces and equipment.
- Position the virtual line perpendicular to the primary movement direction.
- Avoid placing the line near door edges or areas with frequent false detections.
- Use `flip_line` when the natural crossing direction is opposite to the default convention.
- Calibrate using saved frames from `scripts/test_camera.py` to refine coordinates.

[No sources needed since this section provides general guidance]