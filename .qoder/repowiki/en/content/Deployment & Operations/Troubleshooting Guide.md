# Troubleshooting Guide

<cite>
**Referenced Files in This Document**
- [README.md](file://README.md)
- [main.py](file://app/main.py)
- [config.py](file://app/config.py)
- [database.py](file://app/database.py)
- [test_camera.py](file://scripts/test_camera.py)
- [camera_service.py](file://app/services/camera_service.py)
- [yolo_service.py](file://app/services/yolo_service.py)
- [face_service.py](file://app/services/face_service.py)
- [google_sheets_service.py](file://app/services/google_sheets_service.py)
- [event_service.py](file://app/services/event_service.py)
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
This guide provides systematic troubleshooting for the face-date system in production. It focuses on camera connectivity, AI model and runtime issues, performance bottlenecks, event processing problems, Google Sheets synchronization failures, CSV export issues, and API endpoint problems. Each section includes diagnostic steps, relevant log locations, configuration checks, and escalation criteria.

The system runs a FastAPI server that manages cameras, detection/tracking, face recognition, inventory ledgering, events, reporting, and optional Google Sheets sync. Camera capture is decoupled from inference through bounded queues, and AI services degrade gracefully when dependencies are missing.

## Project Structure
At a high level:
- `app/main.py` starts the FastAPI application, registers routers, sets up session middleware, error handlers, and the `/healthz` monitoring endpoint.
- `app/config.py` loads `.env`, builds immutable settings, and exposes warnings for common misconfiguration.
- `app/database.py` configures SQLAlchemy, SQLite WAL mode, and session lifecycle.
- `scripts/test_camera.py` validates camera sources without loading AI models.
- Services under `app/services/` implement camera capture, YOLO detection, InsightFace recognition, Google Sheets sync, and event processing.

```mermaid
graph TB
Client["Browser / External Client"] --> API["FastAPI Server<br/>app/main.py"]
API --> Config["Settings<br/>app/config.py"]
API --> DB["SQLite Database<br/>app/database.py"]
API --> CameraSvc["Camera Service<br/>app/services/camera_service.py"]
API --> YoloSvc["YOLO Service<br/>app/services/yolo_service.py"]
API --> FaceSvc["InsightFace Service<br/>app/services/face_service.py"]
API --> EventSvc["Event Service<br/>app/services/event_service.py"]
API --> SheetsSvc["Google Sheets Sync<br/>app/services/google_sheets_service.py"]
TestScript["test_camera.py<br/>scripts/test_camera.py"] --> CameraSvc
```

**Diagram sources**
- [main.py:44-129](file://app/main.py#L44-L129)
- [config.py:159-228](file://app/config.py#L159-L228)
- [database.py:41-128](file://app/database.py#L41-L128)
- [test_camera.py:58-133](file://scripts/test_camera.py#L58-L133)
- [camera_service.py:226-448](file://app/services/camera_service.py#L226-L448)
- [yolo_service.py:55-272](file://app/services/yolo_service.py#L55-L272)
- [face_service.py:173-341](file://app/services/face_service.py#L173-L341)
- [google_sheets_service.py:82-414](file://app/services/google_sheets_service.py#L82-L414)
- [event_service.py:59-329](file://app/services/event_service.py#L59-L329)

**Section sources**
- [README.md:597-621](file://README.md#L597-L621)
- [main.py:44-129](file://app/main.py#L44-L129)
- [config.py:159-228](file://app/config.py#L159-L228)
- [database.py:41-128](file://app/database.py#L41-L128)

## Core Components
- **FastAPI Application**: Registers routers, session middleware, exception handlers, and health endpoint.
- **Settings**: Loads environment variables, validates paths/providers, and warns about insecure defaults or missing models.
- **Database**: Creates engine with SQLite WAL, foreign keys enabled, busy timeout, and session scope.
- **Camera Worker**: Owns capture thread, handles RTSP/USB/file/simulation, exponential backoff, frame queue, and status transitions.
- **YOLO Service**: Lazy model load, fallback weights, ByteTrack tracking, detection-to-category mapping, and inference metrics.
- **InsightFace Service**: Lazy ONNX Runtime app load, gallery embeddings, cosine similarity matching with threshold/margin, enrollment quality checks.
- **Google Sheets Service**: Background scheduler, idempotent event push by Event ID, retry/backoff, sheet headers, and sync logs.
- **Event Service**: Dedupe key, cooldown window, review workflow (CONFIRMED/REJECTED), pending sync events, dashboard stats.

**Section sources**
- [main.py:44-129](file://app/main.py#L44-L129)
- [config.py:140-228](file://app/config.py#L140-L228)
- [database.py:41-128](file://app/database.py#L41-L128)
- [camera_service.py:226-448](file://app/services/camera_service.py#L226-L448)
- [yolo_service.py:55-272](file://app/services/yolo_service.py#L55-L272)
- [face_service.py:173-341](file://app/services/face_service.py#L173-L341)
- [google_sheets_service.py:82-414](file://app/services/google_sheets_service.py#L82-L414)
- [event_service.py:59-329](file://app/services/event_service.py#L59-L329)

## Architecture Overview
The end-to-end flow for a real camera involves:
1. CameraWorker opens source (RTSP/USB/file) and reads frames into a bounded queue.
2. Inference pipeline uses YOLOService for object detection/tracking and FaceService for face recognition.
3. Association logic links persons to equipment; events are created with dedupe keys and cooldown windows.
4. Events may create pending inventory movements; operators confirm/reject via review workflow.
5. GoogleSheetsService pushes events and reference data to Sheets with retries and idempotency.

```mermaid
sequenceDiagram
participant Cam as "CameraWorker"
participant Q as "Frame Queue"
participant Yolo as "YoloService"
participant Face as "FaceService"
participant Ev as "EventService"
participant Db as "SQLite"
participant Sheets as "GoogleSheetsService"
Cam->>Q : Push raw frame
Q-->>Cam : Drain latest frame
Cam->>Yolo : detect(frame)
Yolo-->>Cam : Detections + track IDs
Cam->>Face : recognize(person crop)
Face-->>Cam : Match result
Cam->>Ev : create_event(...)
Ev->>Db : Insert event + pending movement
Sheets->>Db : Query pending events
Sheets->>Sheets : Append rows with retries
Sheets-->>Db : Update sync status
```

**Diagram sources**
- [camera_service.py:365-427](file://app/services/camera_service.py#L365-L427)
- [yolo_service.py:166-236](file://app/services/yolo_service.py#L166-L236)
- [face_service.py:261-274](file://app/services/face_service.py#L261-L274)
- [event_service.py:59-147](file://app/services/event_service.py#L59-L147)
- [google_sheets_service.py:340-359](file://app/services/google_sheets_service.py#L340-L359)

## Detailed Component Analysis

### Camera Connectivity Diagnostics
Symptoms:
- Camera shows OFFLINE or CONNECTING repeatedly.
- No frames captured; FPS is zero.
- MJPEG stream unavailable.

Diagnostic approach:
- Use `scripts/test_camera.py` to validate source type, URL/device/path, resolution, and FPS without loading AI models.
- Check worker status, frames captured/dropped, last frame timestamp, and status message.
- Verify RTSP credentials, network reachability, H.264 substream availability, USB device index, and firewall/antivirus blocks.
- Inspect camera service logs for open/read errors and reconnection backoff messages.

Resolution steps:
- Confirm source string format: RTSP URL with credentials, `usb:N`, file path, or simulation.
- Reduce processing FPS and image size if frames are dropped due to slow inference.
- Adjust `CAMERA_RECONNECT_INTERVAL` and `CAMERA_MAX_RETRY_INTERVAL` if reconnects are too aggressive or too slow.
- For RTSP, try alternate channel/substream and ensure the camera outputs H.264.

```mermaid
flowchart TD
Start(["Start test_camera.py"]) --> BuildConfig["Build CameraConfig from DB or args"]
BuildConfig --> StartWorker["Start CameraWorker"]
StartWorker --> Loop["Drain frames for N seconds"]
Loop --> FrameAvailable{"Frame received?"}
FrameAvailable --> |No| Backoff["Backoff + OFFLINE status"]
Backoff --> Loop
FrameAvailable --> |Yes| SaveSample["Save sample frames"]
SaveSample --> Report["Report FPS, status, dropped frames"]
Report --> End(["Exit code 0 if frames > 0, else 1"])
```

**Diagram sources**
- [test_camera.py:58-133](file://scripts/test_camera.py#L58-L133)
- [camera_service.py:365-427](file://app/services/camera_service.py#L365-L427)

**Section sources**
- [test_camera.py:1-138](file://scripts/test_camera.py#L1-L138)
- [camera_service.py:82-132](file://app/services/camera_service.py#L82-L132)
- [camera_service.py:226-448](file://app/services/camera_service.py#L226-L448)
- [README.md:217-241](file://README.md#L217-L241)

### AI Model Loading Failures
Symptoms:
- YOLO model not found warning.
- YOLO inference raises model unavailable error.
- InsightFace unavailable; face recognition fails.
- ONNX Runtime errors or GPU provider issues.

Diagnostic approach:
- Check Settings warnings for missing YOLO model path and Google Sheets configuration.
- Inspect YOLO service status: available flag, reason_unavailable, loaded_path, using_fallback, device, image_size, confidence, classes.
- Inspect Face service status: available flag, reason_unavailable, providers, thresholds, gallery size and load time.
- Validate environment: Python 3.11, AI requirements installed, CUDA provider configured for GPU.

Resolution steps:
- Place custom YOLO model at `data/models/equipment_best.pt`; otherwise, fallback to `yolov8s.pt`.
- Install AI dependencies via `requirements-ai.txt`.
- For GPU, install `onnxruntime-gpu` and set `FACE_PROVIDERS=CUDAExecutionProvider`.
- Reload YOLO model via Settings UI; refresh face gallery after adding employee photos.

```mermaid
classDiagram
class YoloService {
+bool available
+string reason_unavailable
+dict status()
+bool ensure_loaded()
+list detect(frame, confidence, use_tracking, allow_fallback_categories)
+bool reload(rules)
}
class FaceService {
+bool available
+string reason_unavailable
+dict status()
+bool ensure_loaded()
+list detect_faces(image)
+FaceMatch recognize(image)
+EnrollmentResult create_embedding(image)
}
YoloService --> Settings : "uses yolo_device, yolo_image_size, yolo_confidence"
FaceService --> Settings : "uses face_providers, face_detection_size"
```

**Diagram sources**
- [yolo_service.py:55-272](file://app/services/yolo_service.py#L55-L272)
- [face_service.py:173-341](file://app/services/face_service.py#L173-L341)
- [config.py:177-222](file://app/config.py#L177-L222)

**Section sources**
- [config.py:140-156](file://app/config.py#L140-L156)
- [yolo_service.py:77-145](file://app/services/yolo_service.py#L77-L145)
- [face_service.py:184-234](file://app/services/face_service.py#L184-L234)
- [README.md:97-116](file://README.md#L97-L116)

### Performance Bottlenecks
Symptoms:
- Low FPS, high CPU usage, memory growth over time.
- Frames dropped frequently.
- Slow inference times.

Diagnostic approach:
- Monitor worker FPS and dropped frames via `test_camera.py` output and camera health.
- Check YOLO inference metrics: `inference_count`, `last_inference_ms`, `device`, `image_size`.
- Check Face service providers and detection size.
- Review Settings: `PROCESSING_FPS`, `PROCESSING_WIDTH`, `PROCESSING_HEIGHT`, `FACE_RECOGNITION_INTERVAL`, `MAX_FRAME_QUEUE`.

Resolution steps:
- Lower `PROCESSING_FPS` to 5–8 and reduce `YOLO_IMAGE_SIZE` to 480.
- Increase `FACE_RECOGNITION_INTERVAL` to reduce per-frame face work.
- Enable GPU acceleration where supported (`CUDAExecutionProvider`).
- Tune `MAX_FRAME_QUEUE` to balance latency vs. dropped frames.
- Ensure evidence retention and JPEG quality do not overload disk I/O.

```mermaid
flowchart TD
PerfStart["Observe low FPS/high CPU"] --> CheckSettings["Check PROCESSING_FPS, WIDTH, HEIGHT, FACE_INTERVAL"]
CheckSettings --> CheckAI["Check YOLO device/image_size, Face providers"]
CheckAI --> Optimize["Reduce FPS/size, increase interval, enable GPU"]
Optimize --> Recheck["Re-run test_camera.py and monitor dropped frames"]
Recheck --> Done["Stable FPS, lower CPU, fewer drops"]
```

**Diagram sources**
- [config.py:190-210](file://app/config.py#L190-L210)
- [yolo_service.py:178-201](file://app/services/yolo_service.py#L178-L201)
- [face_service.py:222-228](file://app/services/face_service.py#L222-L228)
- [test_camera.py:105-123](file://scripts/test_camera.py#L105-L123)

**Section sources**
- [config.py:190-210](file://app/config.py#L190-L210)
- [yolo_service.py:178-201](file://app/services/yolo_service.py#L178-L201)
- [face_service.py:222-228](file://app/services/face_service.py#L222-L228)
- [test_camera.py:105-123](file://scripts/test_camera.py#L105-L123)

### Event Processing Issues
Symptoms:
- Duplicate events appearing.
- Missed detections or associations.
- Incorrect person-object associations.
- Review workflow stuck in PENDING.

Diagnostic approach:
- Inspect event dedupe key construction and cooldown bucketing.
- Check verification status and association confidence.
- Review pending reviews and pending sync events.
- Validate inventory candidate creation and confirmation/rejection flows.

Resolution steps:
- Adjust `EVENT_COOLDOWN_SECONDS` to reduce rapid duplicates.
- Improve detection/recognition accuracy (model calibration, thresholds).
- Use review workflow to CONFIRM or REJECT events; rejected events do not affect balances.
- Ensure association confidence thresholds are appropriate; ambiguous cases should require review.

```mermaid
flowchart TD
Detect["Detection + Tracking"] --> Associate["Associate person + equipment"]
Associate --> Score{"Association score >= threshold?"}
Score --> |Yes| CreateEvent["Create event with dedupe_key"]
Score --> |No| FlagReview["Flag requires_review"]
CreateEvent --> Cooldown{"Within cooldown bucket?"}
Cooldown --> |Yes| Ignore["Ignore duplicate"]
Cooldown --> |No| Persist["Persist event + pending movement"]
Persist --> Review["Operator CONFIRM/REJECT"]
Review --> Ledger["Update inventory ledger"]
```

**Diagram sources**
- [event_service.py:36-56](file://app/services/event_service.py#L36-L56)
- [event_service.py:59-147](file://app/services/event_service.py#L59-L147)
- [event_service.py:150-211](file://app/services/event_service.py#L150-L211)

**Section sources**
- [event_service.py:36-56](file://app/services/event_service.py#L36-L56)
- [event_service.py:59-147](file://app/services/event_service.py#L59-L147)
- [event_service.py:150-211](file://app/services/event_service.py#L150-L211)
- [README.md:316-364](file://README.md#L316-L364)

### Google Sheets Synchronization Failures
Symptoms:
- Sheets sync shows FAILED or no updates.
- Missing Employee/Equipment/MovementEvents sheets.
- Duplicate rows or missing events.

Diagnostic approach:
- Check Sheets service status: enabled, configured, spreadsheet ID, service account file, last run, last result summary.
- Validate `.env`: `GOOGLE_SHEETS_ENABLED`, `GOOGLE_SPREADSHEET_ID`, `GOOGLE_SERVICE_ACCOUNT_FILE`.
- Inspect SyncLog entries and pending/failed events.
- Verify Google Cloud Console permissions: Sheets API enabled, service account granted Editor access.

Resolution steps:
- Correct service account JSON path and ensure file exists.
- Share spreadsheet with service account email as Editor.
- Manually trigger sync via CLI or Reports page; check `/api/sheets/status`.
- Retry failed events; they are retried with exponential backoff.

```mermaid
sequenceDiagram
participant Scheduler as "SheetsScheduler"
participant Service as "GoogleSheetsService"
participant Session as "SQLAlchemy Session"
participant GSheet as "Google Sheets API"
Scheduler->>Service : sync(session, triggered_by="scheduler")
Service->>Session : Load employees/equipment/events/inventory
Service->>GSheet : Authorize + Open Spreadsheet
GSheet-->>Service : Worksheet references
Service->>GSheet : Upsert Employees/Equipment
Service->>GSheet : Append MovementEvents (idempotent by Event ID)
GSheet-->>Service : Success/Failure
Service->>Session : Update SyncLog + event sync status
```

**Diagram sources**
- [google_sheets_service.py:362-414](file://app/services/google_sheets_service.py#L362-L414)
- [google_sheets_service.py:234-296](file://app/services/google_sheets_service.py#L234-L296)
- [google_sheets_service.py:340-359](file://app/services/google_sheets_service.py#L340-L359)

**Section sources**
- [google_sheets_service.py:82-130](file://app/services/google_sheets_service.py#L82-L130)
- [google_sheets_service.py:234-296](file://app/services/google_sheets_service.py#L234-L296)
- [google_sheets_service.py:340-359](file://app/services/google_sheets_service.py#L340-L359)
- [README.md:368-413](file://README.md#L368-L413)

### CSV Export and API Endpoint Problems
Symptoms:
- CSV download missing columns or garbled text.
- API endpoints return 401/403 or validation errors.
- Health endpoint not responding.

Diagnostic approach:
- Confirm CSV export endpoint returns UTF-8 BOM and expected columns.
- Check authentication roles and CSRF protection for POST requests.
- Validate `/healthz` response for monitoring integration.
- Review exception handlers for API vs template responses.

Resolution steps:
- Ensure HTTPS in production for session cookies (`https_only`).
- Fix request payloads to match API schemas.
- Restart server if internal exceptions occur; inspect logs for tracebacks.

**Section sources**
- [README.md:417-427](file://README.md#L417-L427)
- [main.py:70-129](file://app/main.py#L70-L129)
- [README.md:197-199](file://README.md#L197-L199)

## Dependency Analysis
Key dependencies and relationships:
- FastAPI depends on routers for pages/auth/cameras/employees/equipment/events/reports/settings.
- CameraWorker depends on SourceReader implementations (OpenCVSource/SimulationSource).
- YOLOService depends on Ultralytics and optionally ByteTrack tracker files.
- FaceService depends on InsightFace and ONNX Runtime providers.
- GoogleSheetsService depends on gspread and Google OAuth service accounts.
- EventService depends on InventoryService and AuditService for ledgering and audit trails.

```mermaid
graph LR
Main["app/main.py"] --> Routers["Routers (pages, auth, cameras, ...)"]
Main --> Config["app/config.py"]
Main --> DB["app/database.py"]
Routers --> CameraSvc["app/services/camera_service.py"]
Routers --> YoloSvc["app/services/yolo_service.py"]
Routers --> FaceSvc["app/services/face_service.py"]
Routers --> EventSvc["app/services/event_service.py"]
Routers --> SheetsSvc["app/services/google_sheets_service.py"]
CameraSvc --> CV2["OpenCV"]
YoloSvc --> Ultralytics["Ultralytics YOLO"]
FaceSvc --> InsightFace["InsightFace + ONNX Runtime"]
SheetsSvc --> GSpread["gspread + Google Auth"]
```

**Diagram sources**
- [main.py:44-68](file://app/main.py#L44-L68)
- [camera_service.py:82-220](file://app/services/camera_service.py#L82-L220)
- [yolo_service.py:23-30](file://app/services/yolo_service.py#L23-L30)
- [face_service.py:29-35](file://app/services/face_service.py#L29-L35)
- [google_sheets_service.py:113-129](file://app/services/google_sheets_service.py#L113-L129)

**Section sources**
- [main.py:44-68](file://app/main.py#L44-L68)
- [camera_service.py:82-220](file://app/services/camera_service.py#L82-L220)
- [yolo_service.py:23-30](file://app/services/yolo_service.py#L23-L30)
- [face_service.py:29-35](file://app/services/face_service.py#L29-L35)
- [google_sheets_service.py:113-129](file://app/services/google_sheets_service.py#L113-L129)

## Performance Considerations
- Prefer GPU acceleration for both YOLO and InsightFace where hardware supports it.
- Tune processing FPS and image dimensions to balance throughput and accuracy.
- Avoid excessive face recognition frequency; adjust `FACE_RECOGNITION_INTERVAL`.
- Monitor frame queue depth and dropped frames; tune `MAX_FRAME_QUEUE`.
- Evidence retention policies can impact disk I/O; adjust `EVIDENCE_RETENTION_DAYS` and `EVIDENCE_JPEG_QUALITY`.

[No sources needed since this section provides general guidance]

## Troubleshooting Guide

### Systematic Diagnostic Approach
1. **Health Check**: Call `/healthz` to verify server status and running cameras.
2. **Configuration Validation**: Review Settings warnings for missing models, secrets, or Sheets configuration.
3. **Camera Test**: Run `scripts/test_camera.py` with appropriate arguments to isolate source issues.
4. **AI Status**: Check YOLO and Face service statuses for availability and reasons.
5. **Event Review**: Inspect pending reviews and sync status for stuck events.
6. **Logs**: Analyze `logs/app.log` for tracebacks and detailed error messages.

**Section sources**
- [main.py:116-123](file://app/main.py#L116-L123)
- [config.py:140-156](file://app/config.py#L140-L156)
- [test_camera.py:58-133](file://scripts/test_camera.py#L58-L133)
- [yolo_service.py:77-103](file://app/services/yolo_service.py#L77-L103)
- [face_service.py:184-204](file://app/services/face_service.py#L184-L204)
- [event_service.py:282-289](file://app/services/event_service.py#L282-L289)

### Common Issues and Resolutions

#### RTSP Authentication Failures
- Symptom: Camera OFFLINE with connection errors.
- Steps:
  - Verify RTSP URL includes correct username/password.
  - Test with `test_camera.py --source rtsp://user:pass@host:port/stream`.
  - Try alternate substream/channel.
  - Check network connectivity and firewall rules.

**Section sources**
- [camera_service.py:99-116](file://app/services/camera_service.py#L99-L116)
- [test_camera.py:125-132](file://scripts/test_camera.py#L125-L132)
- [README.md:217-241](file://README.md#L217-L241)

#### USB Device Access Issues
- Symptom: USB camera not detected or frames not captured.
- Steps:
  - Use `test_camera.py --type usb --source 0` (try other indices).
  - Ensure OS grants access to USB device.
  - Check antivirus/firewall blocking OpenCV.

**Section sources**
- [camera_service.py:95-98](file://app/services/camera_service.py#L95-L98)
- [test_camera.py:125-132](file://scripts/test_camera.py#L125-L132)

#### Network Connectivity Problems
- Symptom: Intermittent disconnections or timeouts.
- Steps:
  - Ping camera IP and verify routing.
  - Adjust reconnect intervals and max retry intervals.
  - Monitor dropped frames and last frame timestamps.

**Section sources**
- [camera_service.py:365-427](file://app/services/camera_service.py#L365-L427)
- [config.py:200-201](file://app/config.py#L200-L201)

#### YOLO Model Loading Failures
- Symptom: Warning “YOLO modeli topilmadi” or inference errors.
- Steps:
  - Place custom model at `data/models/equipment_best.pt`.
  - Install AI dependencies.
  - Reload model via Settings UI.

**Section sources**
- [config.py:149-150](file://app/config.py#L149-L150)
- [yolo_service.py:115-145](file://app/services/yolo_service.py#L115-L145)
- [README.md:516-521](file://README.md#L516-L521)

#### InsightFace Recognition Problems
- Symptom: Face recognition unavailable or unknown person results.
- Steps:
  - Install `requirements-ai.txt`.
  - Configure `FACE_PROVIDERS` for GPU if available.
  - Refresh face gallery after adding employee photos.
  - Calibrate `FACE_SIMILARITY_THRESHOLD` and `FACE_MATCH_MARGIN`.

**Section sources**
- [face_service.py:207-234](file://app/services/face_service.py#L207-L234)
- [config.py:194-199](file://app/config.py#L194-L199)
- [README.md:270-277](file://README.md#L270-L277)

#### ONNX Runtime Errors
- Symptom: ONNX provider initialization failure.
- Steps:
  - Install `onnxruntime-gpu` for CUDA.
  - Set `FACE_PROVIDERS=CUDAExecutionProvider`.
  - Verify CUDA toolkit compatibility.

**Section sources**
- [face_service.py:222-228](file://app/services/face_service.py#L222-L228)
- [README.md:114-116](file://README.md#L114-L116)

#### GPU Acceleration Issues
- Symptom: Slow inference or GPU not utilized.
- Steps:
  - Confirm CUDA provider in settings.
  - Check device selection for YOLO (`YOLO_DEVICE`).
  - Monitor inference metrics.

**Section sources**
- [config.py:190-193](file://app/config.py#L190-L193)
- [yolo_service.py:178-184](file://app/services/yolo_service.py#L178-L184)

#### Low FPS and High CPU Usage
- Symptom: System overloaded, frames dropped.
- Steps:
  - Reduce `PROCESSING_FPS`, `YOLO_IMAGE_SIZE`, increase `FACE_RECOGNITION_INTERVAL`.
  - Enable GPU acceleration.
  - Tune `MAX_FRAME_QUEUE`.

**Section sources**
- [config.py:190-210](file://app/config.py#L190-L210)
- [test_camera.py:105-123](file://scripts/test_camera.py#L105-L123)

#### Memory Leaks
- Symptom: Memory grows over time.
- Steps:
  - Ensure evidence retention policy is reasonable.
  - Monitor long-running processes and restart if necessary.
  - Check for unbounded caches or large galleries.

**Section sources**
- [config.py:208-210](file://app/config.py#L208-L210)
- [face_service.py:132-166](file://app/services/face_service.py#L132-L166)

#### Database Locking Conflicts
- Symptom: “database is locked” errors.
- Steps:
  - Ensure only one server instance writes to SQLite.
  - Use short transactions and avoid concurrent writers.
  - Consider PostgreSQL for multi-worker setups.

**Section sources**
- [database.py:49-58](file://app/database.py#L49-L58)
- [README.md:525-526](file://README.md#L525-L526)

#### Duplicate Events
- Symptom: Same movement logged multiple times.
- Steps:
  - Adjust `EVENT_COOLDOWN_SECONDS`.
  - Verify dedupe key construction and cooldown buckets.

**Section sources**
- [event_service.py:36-56](file://app/services/event_service.py#L36-L56)
- [event_service.py:87-103](file://app/services/event_service.py#L87-L103)

#### Missed Detections
- Symptom: Objects or persons not detected.
- Steps:
  - Calibrate YOLO confidence and image size.
  - Improve lighting and camera angle.
  - Use simulation mode to validate pipeline.

**Section sources**
- [yolo_service.py:166-236](file://app/services/yolo_service.py#L166-L236)
- [camera_service.py:134-220](file://app/services/camera_service.py#L134-L220)

#### Incorrect Person-Object Associations
- Symptom: Wrong person linked to equipment movement.
- Steps:
  - Review association confidence and thresholds.
  - Use review workflow to correct associations.
  - Improve tracking stability (ByteTrack).

**Section sources**
- [event_service.py:292-298](file://app/services/event_service.py#L292-L298)
- [yolo_service.py:188-199](file://app/services/yolo_service.py#L188-L199)

#### Review Workflow Problems
- Symptom: Events stuck in PENDING.
- Steps:
  - Confirm or reject events via review interface.
  - Provide reasons for rejection.
  - Ensure audit trail records actions.

**Section sources**
- [event_service.py:150-211](file://app/services/event_service.py#L150-L211)

#### Google Sheets Synchronization Failures
- Symptom: Sync FAILED or missing data.
- Steps:
  - Validate service account and spreadsheet permissions.
  - Check sync status and logs.
  - Retry failed events.

**Section sources**
- [google_sheets_service.py:82-130](file://app/services/google_sheets_service.py#L82-L130)
- [google_sheets_service.py:234-296](file://app/services/google_sheets_service.py#L234-L296)

#### CSV Export Issues
- Symptom: Garbled text or missing columns.
- Steps:
  - Ensure UTF-8 BOM encoding.
  - Verify column order and filters.

**Section sources**
- [README.md:417-427](file://README.md#L417-L427)

#### API Endpoint Problems
- Symptom: 401/403 errors or validation failures.
- Steps:
  - Check authentication and roles.
  - Validate request payloads.
  - Inspect exception handlers.

**Section sources**
- [main.py:70-114](file://app/main.py#L70-L114)
- [README.md:197-199](file://README.md#L197-L199)

### When to Escalate
- Persistent camera connectivity issues despite correct credentials and network.
- AI model loading failures after dependency installation and configuration.
- Repeated database locking conflicts indicating multi-writer setup.
- Google Sheets sync failures after permission and configuration corrections.
- Unhandled exceptions causing server instability.

**Section sources**
- [main.py:104-114](file://app/main.py#L104-L114)
- [README.md:525-529](file://README.md#L525-L529)

## Conclusion
This guide provides a structured approach to diagnosing and resolving common production issues in the face-date system. By leveraging configuration checks, diagnostic scripts, service status inspection, and log analysis, most issues can be resolved efficiently. For complex or persistent problems, escalate with detailed logs and configuration details to ensure timely resolution.

[No sources needed since this section summarizes without analyzing specific files]

## Appendices

### Diagnostic Commands
- Camera test: `python scripts/test_camera.py --code CAM-01`
- USB test: `python scripts/test_camera.py --type usb --source 0 --seconds 5`
- Simulation test: `python scripts/test_camera.py --type simulation --seconds 6`
- Sheets sync: `python scripts/sync_google_sheets.py`
- Sheets status: `python scripts/sync_google_sheets.py --status`

**Section sources**
- [README.md:454-471](file://README.md#L454-L471)
- [test_camera.py:58-71](file://scripts/test_camera.py#L58-L71)

### Known Limitations and Workarounds
- Track IDs are per-camera; cross-camera person tracking is not supported.
- Equipment counting is at category level unless operator binds specific assets.
- Only line crossing is supported; zone-based detection is not implemented.
- SQLite is single-server; consider PostgreSQL for multi-worker environments.
- Notifications (Telegram/SMS) are not implemented; rely on dashboards and reports.
- `EQUIPMENT_RETURNED` type exists but is not auto-generated; use inventory operations.

**Section sources**
- [README.md:584-594](file://README.md#L584-L594)