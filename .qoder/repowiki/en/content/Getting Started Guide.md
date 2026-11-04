# Getting Started Guide

<cite>
**Referenced Files in This Document**
- [README.md](file://README.md)
- [run.bat](file://run.bat)
- [run.sh](file://run.sh)
- [run.py](file://run.py)
- [requirements.txt](file://requirements.txt)
- [requirements-ai.txt](file://requirements-ai.txt)
- [app/config.py](file://app/config.py)
</cite>

## Table of Contents
1. [Introduction](#introduction)
2. [System Requirements](#system-requirements)
3. [Installation](#installation)
4. [First-Time Setup](#first-time-setup)
5. [AI Packages and Real Detection](#ai-packages-and-real-detection)
6. [Camera Setup Options](#camera-setup-options)
7. [Running the System](#running-the-system)
8. [Initial System Validation](#initial-system-validation)
9. [Troubleshooting](#troubleshooting)
10. [Quick Reference](#quick-reference)

## Introduction
This guide helps you install, configure, and run the face-date system on Windows, Linux, or macOS. The system provides a local dashboard for camera monitoring, employee face recognition, equipment detection, movement logging, inventory tracking, and reporting. You can start with simulation mode without real cameras, then add RTSP, USB, file, or simulated sources as needed.

The fastest way to start is using the provided launch scripts. If you prefer manual control, step-by-step instructions are included for all platforms.

## System Requirements
- **Python 3.11 (64-bit)**: Required for reliable AI package wheels. Other versions may not work with `torch` or `insightface`.
- **Operating systems**: Windows 10/11, Linux, or macOS.
- **Hardware**:
  - Minimum RAM: 4 GB.
  - Recommended RAM: 8 GB.
  - Disk space: At least 2 GB free for models and dependencies.
- **GPU (optional)**: CUDA-enabled GPU improves FPS for YOLO and face recognition.
- **Network**: Internet access is only required for downloading AI models and Google Sheets integration. Core operation can continue offline once installed.

**Section sources**
- [README.md:37-47](file://README.md#L37-L47)

## Installation

### Windows — recommended quick start
Use the provided batch script to create a virtual environment, copy `.env`, install core packages, and start the server.

```bat
run.bat
```

What it does:
- Creates `.venv` if missing.
- Copies `.env.example` to `.env` if `.env` is missing.
- Installs core dependencies from `requirements.txt`.
- Starts the server via `run.py`.

After starting, open your browser at:
```
http://127.0.0.1:8000
```

If Python 3.11 is not found, the script will show an error and exit. Install Python 3.11 and ensure `python.exe` is available in PATH.

**Section sources**
- [README.md:53-63](file://README.md#L53-L63)
- [run.bat:1-64](file://run.bat#L1-L64)

### Linux/macOS — recommended quick start
Make the shell script executable and run it.

```bash
chmod +x run.sh
./run.sh
```

What it does:
- Creates `.venv` if missing.
- Copies `.env.example` to `.env` if `.env` is missing.
- Installs core dependencies from `requirements.txt`.
- Starts the server via `run.py`.

Open your browser at:
```
http://127.0.0.1:8000
```

**Section sources**
- [README.md:65-70](file://README.md#L65-L70)
- [run.sh:1-45](file://run.sh#L1-L45)

### Manual installation (all platforms)

#### Create a Python 3.11 virtual environment
Windows (PowerShell or cmd):
```bat
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env
.venv\Scripts\python.exe run.py
```

Linux/macOS:
```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
cp .env.example .env
.venv/bin/python run.py
```

Notes:
- `run.py` supports `--host`, `--port`, `--reload`, and `--log-level`.
- Example custom port:
  ```bat
  .venv\Scripts\python.exe run.py --port 9000 --reload
  ```

**Section sources**
- [README.md:72-96](file://README.md#L72-L96)
- [run.py:1-44](file://run.py#L1-L44)

## First-Time Setup

### Configure `.env`
Before first use, edit `.env`:
- Change `APP_SECRET_KEY` to a secure random value.
- Change `BOOTSTRAP_ADMIN_PASSWORD` before creating the initial admin account.
- Adjust host/port if needed (`APP_HOST`, `APP_PORT`).
- Review other settings such as timezone, model paths, thresholds, evidence retention, and Google Sheets options.

The application loads `.env` automatically when present.

### Initial admin account creation
When the database is empty, the bootstrap admin user is created using values from `.env`:
- Username: `BOOTSTRAP_ADMIN_USERNAME`
- Password: `BOOTSTRAP_ADMIN_PASSWORD`
- Full name: `BOOTSTRAP_ADMIN_FULLNAME`

Default credentials are:
- Username: `admin`
- Password: `Admin123!`

Important: Change the default admin password after first login.

### Database and defaults
On first run:
- SQLite database is created at `data/inventory.db`.
- Default categories are initialized.
- A sample simulation camera named `SIM-01` is added.

You can verify basic health through the health endpoint:
```
GET /healthz
```

**Section sources**
- [README.md:119-134](file://README.md#L119-L134)
- [app/config.py:159-222](file://app/config.py#L159-L222)

## AI Packages and Real Detection

### When to install AI packages
The dashboard, inventory, and review features work without AI packages. Real camera inference requires:
- YOLO-based equipment detection.
- InsightFace-based face recognition.

Install optional AI dependencies:
```bat
.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
```

### Windows build notes
- `ultralytics` pulls in `torch`. CPU wheels are large (~250 MB).
- `insightface` may require Visual Studio Build Tools because it compiles Cython extensions.
- If building fails, the system still starts and reports face recognition as unavailable rather than pretending it works.
- Face models are downloaded on first use under `~/.insightface`.

### GPU setup (optional)
For CUDA acceleration:
- Install `onnxruntime-gpu` instead of `onnxruntime`.
- Set `FACE_PROVIDERS=CUDAExecutionProvider` in `.env`.

**Section sources**
- [README.md:97-116](file://README.md#L97-L116)
- [requirements-ai.txt:1-19](file://requirements-ai.txt#L1-L19)

## Camera Setup Options

### Supported source types
Add cameras from `/cameras` (admin):
- `rtsp`: RTSP stream URL.
- `usb`: USB webcam device index.
- `file`: Video file path.
- `simulation`: Synthetic frames for end-to-end testing without hardware.

Examples:
```
rtsp://admin:password@192.168.1.64:554/Streaming/Channels/101
usb:0
C:\video\entrance.mp4
```

### Virtual line configuration
- Define a virtual line using pixel coordinates `(x1,y1,x2,y2)`.
- Coordinates are relative to `PROCESSING_WIDTH × PROCESSING_HEIGHT`.
- Use “reverse direction” to swap entry/exit logic.
- For calibration, capture frames and measure pixels; `scripts/test_camera.py` can help visualize frames.

### Live streaming endpoints
- Web interface: `/live`
- MJPEG stream: `GET /api/cameras/<CODE>/stream`
- JPEG snapshot: `GET /api/cameras/<CODE>/snapshot`

### Simulation mode
Simulation mode generates synthetic frames and overlays labels. It is useful for validating pipelines without real cameras.

**Section sources**
- [README.md:202-242](file://README.md#L202-L242)

## Running the System

### Start the server
Use one of these methods:
- Windows: `run.bat`
- Linux/macOS: `./run.sh`
- Manual: `.venv\Scripts\python.exe run.py` or `.venv/bin/python run.py`

Access the web interface:
```
http://127.0.0.1:8000
```

### Development reload
Enable automatic code reloading during development:
```bat
.venv\Scripts\python.exe run.py --reload
```

### Health check
Monitor service status:
```
GET /healthz
```

**Section sources**
- [run.bat:52-64](file://run.bat#L52-L64)
- [run.sh:37-45](file://run.sh#L37-L45)
- [run.py:19-39](file://run.py#L19-L39)
- [README.md:134](file://README.md#L134)

## Initial System Validation

### Verify login and roles
- Open `http://127.0.0.1:8000`.
- Log in with bootstrap admin credentials.
- Confirm that pages load correctly.
- Create operator and viewer accounts under Settings → Users.

### Check configuration warnings
The dashboard shows configuration warnings such as:
- Missing YOLO model.
- Default secret key still set.
- Google Sheets enabled but missing ID or service account file.

Fix warnings by updating `.env` or uploading models.

### Validate camera pipeline
- Add a camera (RTSP, USB, file, or simulation).
- Confirm status changes to `ONLINE`.
- View live feed at `/live`.
- Check events and movements logs.

### Validate inventory flow
- Ensure categories exist.
- Add initial inventory if needed.
- Confirm pending transactions appear and can be reviewed.

**Section sources**
- [README.md:119-134](file://README.md#L119-L134)
- [README.md:202-242](file://README.md#L202-L242)

## Troubleshooting

### Common installation issues

| Symptom | Cause | Resolution |
| --- | --- | --- |
| Python 3.11 not found | Missing or wrong Python version | Install Python 3.11 and ensure PATH includes `python.exe` |
| Package installation fails | Network or wheel compatibility issue | Check internet connection; ensure Python 3.11; retry installation |
| Visual Studio Build Tools missing | `insightface` needs C++ build tools on Windows | Install Visual Studio Build Tools with C++ workload |
| Face recognition unavailable | AI packages not installed or model missing | Install `requirements-ai.txt`; refresh gallery in Settings |
| Model download problems | Network or cache issue | Retry download; check `~/.insightface`; verify network access |
| Port already in use | Another process uses port 8000 | Run with `--port 8011` or stop conflicting process |
| Camera offline | Invalid RTSP URL, authentication, codec, or network | Test with `scripts/test_camera.py`; try H.264 sub-stream |
| Repeated connection failures | Source missing or unstable | Fix source; backoff is applied automatically |
| Database locked | Multiple servers writing to same SQLite DB | Run only one server instance |
| Google Sheets failed | Invalid credentials, spreadsheet ID, or sharing | Verify service account and editor permissions; check sync status |
| Login fails in production | HTTPS-only cookie setting | Access via HTTPS or set `APP_ENV=development` |
| Low FPS or high CPU usage | CPU inference overhead | Lower `PROCESSING_FPS`, reduce `YOLO_IMAGE_SIZE`, increase `FACE_RECOGNITION_INTERVAL`, or enable GPU |
| Unexpected error page | Unhandled exception | Inspect `logs/app.log` for traceback |

### Where to look
- Logs: `logs/app.log`
- Database: `data/inventory.db`
- Evidence images: `data/evidence/YYYY-MM-DD/`
- Models: `data/models/`
- Secrets: `secrets/service_account.json`

**Section sources**
- [README.md:516-530](file://README.md#L516-L530)

## Quick Reference

### Commands

Windows:
```bat
run.bat
.venv\Scripts\python.exe run.py --port 9000 --reload
.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
```

Linux/macOS:
```bash
./run.sh
.venv/bin/python run.py --port 9000 --reload
.venv/bin/python -m pip install -r requirements-ai.txt
```

### URLs
- Web interface: `http://127.0.0.1:8000`
- Health check: `GET /healthz`
- Live feed: `/live`
- Cameras: `/cameras`
- Employees: `/employees`
- Equipment: `/equipment`
- Inventory: `/inventory`
- Movements: `/movements`
- Reports: `/reports`
- Review: `/review`
- Settings: `/settings`

### Key configuration keys
- `APP_SECRET_KEY`
- `BOOTSTRAP_ADMIN_USERNAME`
- `BOOTSTRAP_ADMIN_PASSWORD`
- `APP_HOST`
- `APP_PORT`
- `DATABASE_URL`
- `YOLO_MODEL_PATH`
- `FACE_SIMILARITY_THRESHOLD`
- `FACE_MATCH_MARGIN`
- `FACE_RECOGNITION_INTERVAL`
- `PROCESSING_FPS`
- `SIMULATION_MODE`
- `GOOGLE_SHEETS_ENABLED`

**Section sources**
- [README.md:138-170](file://README.md#L138-L170)
- [README.md:173-199](file://README.md#L173-L199)
- [app/config.py:159-222](file://app/config.py#L159-L222)