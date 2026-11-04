"""Drawing helpers for the live view and evidence stills."""

from __future__ import annotations

import cv2
import numpy as np

from app.models.enums import CameraStatus, Direction
from app.services.types import TrackFeature

COLOR_PERSON = (90, 200, 250)
COLOR_EQUIPMENT = (60, 210, 120)
COLOR_UNKNOWN = (80, 80, 240)
COLOR_LINE = (255, 200, 0)
COLOR_TEXT = (255, 255, 255)


def draw_line(frame: np.ndarray, line, direction: Direction = Direction.NONE) -> None:
    if not line:
        return
    (x1, y1), (x2, y2) = line
    cv2.line(frame, (int(x1), int(y1)), (int(x2), int(y2)), COLOR_LINE, 2)
    cv2.putText(
        frame,
        "KIRISH / CHIQISH",
        (int(x1) + 6, int(y1) - 8),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        COLOR_LINE,
        1,
    )


def draw_tracks(frame: np.ndarray, tracks: list[TrackFeature]) -> None:
    for track in tracks:
        x1, y1, x2, y2 = track.bbox.as_int()
        if track.is_person:
            color = COLOR_PERSON if track.employee_id else COLOR_UNKNOWN
            label = track.employee_name if track.employee_name else "Noma'lum shaxs"
            if track.face_score is not None:
                label = f"{label} {track.face_score:.2f}"
            title = f"#{track.track_id} {label}"
        else:
            color = COLOR_EQUIPMENT
            title = f"#{track.track_id} {track.category} {track.confidence:.2f}"
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        (text_w, text_h), _ = cv2.getTextSize(title, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(frame, (x1, max(0, y1 - text_h - 8)), (x1 + text_w + 8, y1), color, -1)
        cv2.putText(
            frame,
            title,
            (x1 + 4, max(12, y1 - 6)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            (20, 20, 20),
            1,
        )


def draw_status(frame: np.ndarray, camera_name: str, status: CameraStatus, extra: str = "") -> None:
    height, width = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (width, 34), (20, 20, 20), -1)
    text = f"{camera_name}  |  {status.label_uz}"
    if extra:
        text += f"  |  {extra}"
    cv2.putText(frame, text, (10, 23), cv2.FONT_HERSHEY_SIMPLEX, 0.55, COLOR_TEXT, 1)


def annotate(
    frame: np.ndarray,
    *,
    tracks: list[TrackFeature],
    line=None,
    camera_name: str = "",
    status: CameraStatus = CameraStatus.ONLINE,
    notes: str = "",
) -> np.ndarray:
    canvas = frame.copy()
    draw_line(canvas, line)
    draw_tracks(canvas, tracks)
    draw_status(canvas, camera_name, status, notes)
    return canvas


def placeholder_frame(width: int = 960, height: int = 540, message: str = "Kadr yo‘q") -> np.ndarray:
    frame = np.full((height, width, 3), 28, dtype=np.uint8)
    cv2.putText(frame, message, (24, height // 2), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (140, 140, 140), 2)
    return frame
