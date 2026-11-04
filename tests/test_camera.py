"""Camera capture: missing source handling, simulation frames, JPEG snapshots."""

from __future__ import annotations

import time

import numpy as np
import pytest

from app.models.enums import CameraStatus, SourceType
from app.services.camera_service import (
    CameraConfig,
    CameraWorker,
    OpenCVSource,
    SimulationSource,
    build_source,
)


def config(**overrides) -> CameraConfig:
    base = dict(
        id=1,
        code="CAM-01",
        name="Kirish",
        location="Bino kirish",
        source_type=SourceType.FILE.value,
        source="file:data/nonexistent-video.mp4",
        is_enabled=True,
    )
    base.update(overrides)
    return CameraConfig(**base)


def simulation_config() -> CameraConfig:
    return config(source_type=SourceType.SIMULATION.value, source="simulation://demo")


def wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_missing_file_source_reports_unavailable(tmp_path):
    source = OpenCVSource(str(tmp_path / "missing.mp4"), SourceType.FILE.value)
    assert source.open() is False
    assert source.read() is None
    source.release()


def test_camera_worker_without_source_goes_offline_and_recovers_on_stop(settings):
    events: list[tuple[str, CameraStatus]] = []
    worker = CameraWorker(
        config(),
        settings,
        on_status_change=lambda code, status, message: events.append((code, status)),
    )
    worker.start()

    assert wait_for(lambda: worker.status is CameraStatus.OFFLINE)
    assert "Ulanib bo‘lmadi" in worker.status_message
    assert worker.frames_captured == 0
    assert worker.latest_frame() is None
    assert worker.latest_annotated_jpeg() is None

    worker.stop()
    assert worker.is_running is False
    assert worker.status is CameraStatus.DISABLED
    assert (("CAM-01", CameraStatus.CONNECTING) in events) is True
    assert (("CAM-01", CameraStatus.OFFLINE) in events) is True


def test_simulation_source_produces_labelled_frames(settings):
    source = SimulationSource(width=320, height=180, fps=10)
    assert source.open() is True
    frame = source.read()
    assert frame is not None
    assert frame.shape == (180, 320, 3)
    assert source.ground_truth and source.ground_truth[0]["category"] == "person"
    source.release()
    assert source.read() is None


def test_build_source_picks_simulation(settings):
    assert isinstance(build_source(simulation_config(), settings), SimulationSource)
    assert isinstance(build_source(config(), settings), OpenCVSource)


def test_simulation_worker_streams_frames(settings):
    worker = CameraWorker(simulation_config(), settings)
    worker.start()
    try:
        assert wait_for(lambda: worker.status is CameraStatus.ONLINE)
        assert wait_for(lambda: worker.frames_captured > 0)

        frame = worker.drain()
        assert frame is not None
        assert frame.shape[2] == 3

        truth = worker.simulation_ground_truth()
        assert truth and truth[0]["is_person"] is True

        jpeg = worker.latest_annotated_jpeg()
        assert jpeg is not None
        assert jpeg[:2] == b"\xff\xd8"  # JPEG SOI marker
    finally:
        worker.stop()


def test_publish_annotated_replaces_snapshot(settings):
    worker = CameraWorker(simulation_config(), settings)
    frame = np.zeros((120, 160, 3), dtype=np.uint8)
    frame[:] = (10, 20, 30)
    worker.publish_annotated(frame)
    jpeg = worker.latest_annotated_jpeg()
    assert jpeg is not None and jpeg[:2] == b"\xff\xd8"


def test_oversized_frames_are_resized(settings):
    worker = CameraWorker(simulation_config(), settings)
    big = np.zeros((settings.processing_height * 2, settings.processing_width * 2, 3), dtype=np.uint8)
    resized = worker._resize(big)
    assert resized.shape[0] <= settings.processing_height
    assert resized.shape[1] <= settings.processing_width


def test_small_frames_are_kept_as_is(settings):
    worker = CameraWorker(simulation_config(), settings)
    small = np.zeros((120, 160, 3), dtype=np.uint8)
    assert worker._resize(small) is small


@pytest.mark.parametrize("source_type", [SourceType.FILE.value, SourceType.USB.value, SourceType.RTSP.value])
def test_opencv_source_parsing_keeps_raw_value(source_type):
    source = OpenCVSource("usb:2" if source_type == SourceType.USB.value else "rtsp://user@camera/1", source_type)
    assert source.raw
