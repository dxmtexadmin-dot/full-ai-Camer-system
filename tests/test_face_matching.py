"""Face matching logic (no model download required)."""

from __future__ import annotations

import numpy as np
import pytest

from app.services.face_service import UNKNOWN_LABEL, GalleryEntry, expand_person_crop, match_embeddings
from app.services.types import BBox


def vector(*values: float) -> np.ndarray:
    return np.asarray(values, dtype=np.float32)


def test_confident_match_returns_employee():
    gallery = [
        GalleryEntry(1, "Ali Valiyev", vector(1.0, 0.0, 0.0)),
        GalleryEntry(2, "Dilnoza Karimova", vector(0.0, 1.0, 0.0)),
    ]
    result = match_embeddings(vector(0.95, 0.05, 0.0), gallery, threshold=0.45, margin=0.05)
    assert result.matched is True
    assert result.employee_id == 1
    assert result.label == "Ali Valiyev"
    assert result.score > 0.9


def test_unknown_person_when_below_threshold():
    gallery = [GalleryEntry(1, "Ali Valiyev", vector(1.0, 0.0, 0.0))]
    result = match_embeddings(vector(0.0, 1.0, 0.0), gallery, threshold=0.45, margin=0.05)
    assert result.matched is False
    assert result.employee_id is None
    assert result.label == UNKNOWN_LABEL
    assert "chegaradan past" in result.reason


def test_two_similar_employees_are_rejected_as_uncertain():
    gallery = [
        GalleryEntry(1, "Ali Valiyev", vector(1.0, 0.0, 0.0)),
        GalleryEntry(2, "Alisher Karimov", vector(0.99, 0.02, 0.0)),
    ]
    result = match_embeddings(vector(1.0, 0.01, 0.0), gallery, threshold=0.45, margin=0.05)
    assert result.matched is False
    assert result.employee_id is None
    assert "ishonchsiz" in result.reason


def test_best_reference_per_employee_wins():
    gallery = [
        GalleryEntry(1, "Ali Valiyev", vector(1.0, 0.0, 0.0)),
        GalleryEntry(1, "Ali Valiyev", vector(0.0, 1.0, 0.0)),
        GalleryEntry(2, "Dilnoza Karimova", vector(0.0, 0.0, 1.0)),
    ]
    result = match_embeddings(vector(0.0, 1.0, 0.0), gallery, threshold=0.45, margin=0.05)
    assert result.matched is True
    assert result.employee_id == 1
    assert result.score > 0.99


def test_empty_gallery_is_handled():
    result = match_embeddings(vector(1.0, 0.0), [], threshold=0.45, margin=0.05)
    assert result.matched is False
    assert result.label == UNKNOWN_LABEL


def test_dimension_mismatch_is_ignored():
    gallery = [GalleryEntry(1, "Ali Valiyev", vector(1.0, 0.0, 0.0, 0.0))]
    result = match_embeddings(vector(1.0, 0.0), gallery, threshold=0.45, margin=0.0)
    assert result.matched is False


@pytest.mark.parametrize(
    ("bbox", "expected_height_ratio"),
    [((100, 100, 200, 400), 0.55), ((0, 0, 50, 200), 0.55)],
)
def test_expand_person_crop_covers_head_area(bbox, expected_height_ratio):
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    crop = expand_person_crop(frame, BBox(*bbox), top_ratio=expected_height_ratio)
    assert crop is not None
    assert crop.shape[0] == int((bbox[3] - bbox[1]) * expected_height_ratio)


def test_expand_person_crop_outside_frame_returns_none():
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    assert expand_person_crop(frame, BBox(2000, 2000, 2100, 2200)) is None
