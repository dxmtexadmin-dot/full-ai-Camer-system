"""Avtomatik oldindan belgilash (pre-label) yordamchilari.

Model (`ultralytics`) talab qilmaydigan sof funksiyalar sinaladi: sinf nomini
o‘girish, papka bo‘yicha to‘g‘rilash va YOLO `.txt` matematikasi.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import autolabel_dataset as autolabel  # noqa: E402
import train_yolo  # noqa: E402

NAMES = {
    0: "person",
    3: "all_in_one_pc",
    6: "mouse",
    18: "smartphone",
    31: "tv",
}


def _box(label: str, class_id: int, confidence: float = 0.8, xyxy=(10, 10, 50, 40)):
    return {
        "class_id": class_id,
        "label": label,
        "original_label": label,
        "confidence": confidence,
        "xyxy": xyxy,
        "promoted": False,
    }


# -- nomlarni o‘girish ---------------------------------------------------------
def test_normalize_handles_spaces_and_case():
    assert autolabel.normalize("Cell Phone") == "cell_phone"
    assert autolabel.normalize("  LAN-cable ") == "lan_cable"


def test_coco_alias_maps_cell_phone_to_smartphone():
    index = autolabel.build_index(NAMES)

    assert autolabel.resolve_label("cell phone", index) == 18
    assert autolabel.resolve_label("notebook", index) is None  # `laptop` bu jadvalda yo‘q


def test_unknown_label_is_ignored():
    index = autolabel.build_index(NAMES)

    assert autolabel.resolve_label("banana", index) is None


def test_folder_hint_reads_the_category_prefix():
    assert autolabel.folder_hint("mouse__mouse_001") == "mouse"
    assert autolabel.folder_hint("mouse_001") is None


# -- papka bo‘yicha to‘g‘rilash ------------------------------------------------
def test_phone_box_on_a_mouse_picture_becomes_mouse():
    index = autolabel.build_index(NAMES)
    boxes = [_box("smartphone", 18, 0.81)]

    fixed = autolabel.apply_folder_hint(boxes, "mouse", index)

    assert fixed[0]["class_id"] == 6
    assert fixed[0]["label"] == "mouse"
    assert fixed[0]["promoted"] is True
    assert fixed[0]["original_label"] == "smartphone"


def test_a_real_phone_next_to_a_person_is_not_rewritten():
    index = autolabel.build_index(NAMES)
    boxes = [_box("person", 0), _box("tv", 31)]

    fixed = autolabel.apply_folder_hint(boxes, "mouse", index)

    assert [box["label"] for box in fixed] == ["person", "tv"]
    assert not any(box["promoted"] for box in fixed)


def test_missing_hint_leaves_boxes_untouched():
    index = autolabel.build_index(NAMES)
    boxes = [_box("smartphone", 18)]

    assert autolabel.apply_folder_hint(boxes, None, index) == boxes


# -- YOLO format ---------------------------------------------------------------
def test_yolo_lines_are_normalized_and_clipped():
    lines = autolabel.yolo_lines([_box("mouse", 6, xyxy=(-10, 20, 110, 120))], width=100, height=200)

    assert len(lines) == 1
    class_id, cx, cy, width, height = lines[0].split()
    assert class_id == "6"
    assert float(cx) == pytest.approx(0.5)
    assert float(cy) == pytest.approx(0.35)
    assert float(width) == pytest.approx(1.0)
    assert float(height) == pytest.approx(0.5)


def test_degenerate_boxes_are_dropped():
    flat = _box("mouse", 6, xyxy=(5, 5, 5, 40))

    assert autolabel.yolo_lines([flat], width=100, height=100) == []


# -- prodakshn himoyasi --------------------------------------------------------
def test_small_dataset_does_not_replace_the_production_model(tmp_path):
    labels = tmp_path / "labels"
    labels.mkdir()
    for index in range(24):
        (labels / f"f{index}.txt").write_text("6 0.5 0.5 0.1 0.1\n", encoding="utf-8")

    assert train_yolo.labelled_count(labels) == 24
    assert train_yolo.should_deploy(24, deploy_flag=False) is False
    assert train_yolo.should_deploy(24, deploy_flag=True) is True
    assert train_yolo.should_deploy(120, deploy_flag=False) is True


def test_empty_label_files_are_not_counted(tmp_path):
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "one.txt").write_text("6 0.5 0.5 0.1 0.1\n", encoding="utf-8")
    (labels / "two.txt").write_text("", encoding="utf-8")

    assert train_yolo.labelled_count(labels) == 1
    assert train_yolo.labelled_count(tmp_path / "missing") == 0


# -- train/val ajratish -------------------------------------------------------
def test_every_class_keeps_a_validation_frame_and_a_train_frame():
    stems = [f"mouse__mouse_{index:03d}" for index in range(1, 11)]
    stems += [f"all_in_one_pc__aio_{index:03d}" for index in range(1, 6)]

    chosen = train_yolo.plan_val_split(stems, ratio=0.2)

    assert len([stem for stem in chosen if stem.startswith("mouse__")]) == 2
    assert len([stem for stem in chosen if stem.startswith("all_in_one_pc__")]) == 1
    assert set(chosen) <= set(stems)
    assert len(set(chosen)) == len(chosen)


def test_a_single_picture_stays_in_train():
    assert train_yolo.plan_val_split(["mouse__mouse_001"], ratio=0.2) == []
    assert train_yolo.plan_val_split([], ratio=0.2) == []


def test_labelled_frames_are_preferred_for_validation():
    stems = [f"mouse__mouse_{index:03d}" for index in range(1, 11)]
    labelled = {"mouse__mouse_003", "mouse__mouse_004"}

    chosen = train_yolo.plan_val_split(stems, ratio=0.2, labelled=labelled)

    # 10 tadan 2 tasi val ga: belgilanganlari ustuvor.
    assert chosen == ["mouse__mouse_003", "mouse__mouse_004"]


def test_split_moves_images_and_labels_together(tmp_path):
    images = tmp_path / "images" / "train"
    labels = tmp_path / "labels" / "train"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    for index in range(1, 5):
        (images / f"mouse__mouse_{index:03d}.jpg").write_bytes(b"jpeg")
        (labels / f"mouse__mouse_{index:03d}.txt").write_text("6 0.5 0.5 0.1 0.1\n", encoding="utf-8")

    assert train_yolo.move_to_val(tmp_path, 0.25) == 0

    assert len(list((tmp_path / "images" / "val").glob("*.jpg"))) == 1
    assert len(list((tmp_path / "labels" / "val").glob("*.txt"))) == 1
    assert len(list(images.glob("*.jpg"))) == 3


def test_split_is_not_repeated_when_validation_is_not_empty(tmp_path):
    images = tmp_path / "images" / "train"
    val_images = tmp_path / "images" / "val"
    images.mkdir(parents=True)
    val_images.mkdir(parents=True)
    for index in range(1, 5):
        (images / f"mouse__mouse_{index:03d}.jpg").write_bytes(b"jpeg")
    (val_images / "mouse__mouse_009.jpg").write_bytes(b"jpeg")

    # Takroriy yugurtirish `val` ni shishirib yubormasin.
    assert train_yolo.move_to_val(tmp_path, 0.2) == 1
    assert len(list(images.glob("*.jpg"))) == 4
    assert train_yolo.move_to_val(tmp_path, 0.2, force=True) == 0
    assert len(list(images.glob("*.jpg"))) == 3


# -- person himoyasi ----------------------------------------------------------
def test_person_labels_are_found(tmp_path):
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "frame.txt").write_text("6 0.5 0.5 0.1 0.1\n0 0.3 0.4 0.2 0.5\n", encoding="utf-8")

    assert train_yolo.has_person_labels(labels) is True


def test_dataset_without_person_is_detected(tmp_path):
    # YOLO-DeskDataset'dan olingan kadrlarda faqat texnika bor.
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "frame.txt").write_text("6 0.5 0.5 0.1 0.1\n5 0.2 0.2 0.3 0.1\n", encoding="utf-8")

    assert train_yolo.has_person_labels(labels) is False
    assert train_yolo.has_person_labels(tmp_path / "missing") is False


# -- sinf bo'yicha dataset hisobi ---------------------------------------------
def test_class_frames_are_counted_per_class(tmp_path):
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "a.txt").write_text("6 0.5 0.5 0.1 0.1\n6 0.2 0.2 0.1 0.1\n", encoding="utf-8")
    (labels / "b.txt").write_text("5 0.5 0.5 0.3 0.1\n", encoding="utf-8")
    (labels / "empty.txt").write_text("", encoding="utf-8")

    counts = train_yolo.count_class_frames(labels, len(train_yolo.CLASS_NAMES))

    # `a.txt` da `mouse` ikki marta, lekin kadr bitta.
    assert counts[6] == 1
    assert counts[5] == 1
    assert counts[0] == 0
    assert sum(counts.values()) == 2


def test_class_frames_ignore_broken_and_unknown_ids(tmp_path):
    labels = tmp_path / "labels"
    labels.mkdir()
    (labels / "c.txt").write_text(
        "x 0.5 0.5 0.1 0.1\n999 0.5 0.5 0.1 0.1\n4.0 0.4 0.4 0.2 0.2\n", encoding="utf-8"
    )

    counts = train_yolo.count_class_frames(labels, len(train_yolo.CLASS_NAMES))

    assert counts[4] == 1  # `4.0` ham to'g'ri o'qiladi
    assert sum(counts.values()) == 1


def test_empty_class_table_for_missing_folder(tmp_path):
    counts = train_yolo.count_class_frames(tmp_path / "missing", 27)

    assert len(counts) == 27
    assert set(counts.values()) == {0}
    assert train_yolo.MIN_TRAIN_FRAMES_PER_CLASS > 0

