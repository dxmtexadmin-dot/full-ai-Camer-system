"""Live camera QR reading (§9.2-§9.4): the monitor is fed synthetic frames.

No real camera is involved, so nothing here claims hardware verification - the
frames are built from the same PNG the printer uses, which is enough to prove
that a label shown to the camera is decoded and stored.
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from app.models.enums import AssetMovementType, VerificationStatus
from app.services import asset_monitor, asset_service, qr_service

pytest.importorskip("cv2")


def camera_frame(png: bytes | None, *, width: int = 960, height: int = 720) -> np.ndarray:
    """White frame with an optional label pasted in the middle (BGR, like OpenCV)."""
    canvas = Image.new("RGB", (width, height), "white")
    if png is not None:
        label = Image.open(io.BytesIO(png)).convert("RGB")
        label = label.resize((300, 300), Image.NEAREST)
        canvas.paste(label, ((width - 300) // 2, (height - 300) // 2))
    return np.array(canvas)[:, :, ::-1]


@pytest.fixture()
def monitor():
    from app.database import session_scope

    return asset_monitor.AssetMonitor("CAM-QR", session_scope, interval_seconds=0)


def observe(monitor, frame) -> dict:
    monitor._last_run = 0.0  # bypass the throttle so the test stays fast
    return monitor.observe(frame)


def test_token_is_extracted_from_the_scanned_url():
    token = "0123456789abcdef0123456789abcdef"
    assert asset_monitor.token_from_text(f"http://10.0.0.5:8000/qr/{token}") == token
    assert asset_monitor.token_from_text(token.upper()) == token
    assert asset_monitor.token_from_text("https://example.test/") == ""
    assert asset_monitor.token_from_text("") == ""


def test_camera_reads_the_label_and_records_the_device(session, asset, monitor):
    token = asset_service.get_qr(session, asset.id).token
    png = qr_service.render_qr_png(qr_service.qr_payload("http://10.0.0.5:8000", token), target_px=600)

    stats = observe(monitor, camera_frame(png))
    assert stats["scanned"] == 1

    movements = asset_service.movement_history(session, asset.id)
    assert movements, "the camera read must be stored as a movement"
    assert movements[0].movement_type == AssetMovementType.QR_SCANNED.value
    assert movements[0].camera_code == "CAM-QR"
    assert movements[0].verification_status == VerificationStatus.CONFIRMED.value

    usage = asset_service.usage_history(session, asset.id)
    assert "QR_SCANNED" in [row.action for row in usage]
    assert monitor.health()["visible_labels"] == 1


def test_device_that_leaves_the_view_is_reported_for_review(session, asset, monitor):
    token = asset_service.get_qr(session, asset.id).token
    png = qr_service.render_qr_png(qr_service.qr_payload("http://10.0.0.5:8000", token), target_px=600)
    observe(monitor, camera_frame(png))

    empty = camera_frame(None)
    first = observe(monitor, empty)
    assert first["removed"] == 0, "one missed frame must not raise an alarm"
    second = observe(monitor, empty)
    assert second["removed"] == 1

    movements = [
        item for item in asset_service.movement_history(session, asset.id) if item.movement_type == AssetMovementType.TAKEN_OUT.value
    ]
    assert movements, "removal must produce an event for the review queue"
    assert movements[0].verification_status in {
        VerificationStatus.AUTO.value,
        VerificationStatus.PENDING.value,
    }
    # ...and it still must not change the official owner.
    session.expire_all()
    assert session.get(type(asset), asset.id).responsible_employee_id is None


def test_unknown_label_is_ignored_quietly(session, monitor):
    png = qr_service.render_qr_png("http://10.0.0.5:8000/qr/" + "f" * 32, target_px=600)
    stats = observe(monitor, camera_frame(png))

    assert stats["scanned"] == 0
    assert asset_service.movement_history(session) == []


def test_blank_frame_records_nothing(session, monitor):
    stats = observe(monitor, camera_frame(None))
    assert stats == {"scanned": 0, "unreadable": 0, "removed": 0}
    assert asset_service.movement_history(session) == []


def test_monitor_is_opt_in_and_off_by_default():
    from app.database import session_scope

    off = SimpleNamespace(asset_monitor_enabled=False, asset_monitor_interval=5)
    on = SimpleNamespace(asset_monitor_enabled=True, asset_monitor_interval=7)

    assert asset_monitor.build("CAM-01", session_scope, off) is None
    monitor = asset_monitor.build("CAM-01", session_scope, on)
    assert isinstance(monitor, asset_monitor.AssetMonitor)
    assert monitor.interval == 7


def test_camera_pipeline_keeps_the_feature_disabled():
    """The camera pipeline must not start a QR reader unless the operator opts in."""
    from app.config import get_settings
    from app.database import session_scope

    settings = get_settings()
    assert settings.asset_monitor_enabled is False
    assert asset_monitor.build("CAM-01", session_scope, settings) is None
