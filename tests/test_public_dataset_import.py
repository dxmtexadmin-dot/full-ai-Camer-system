"""Ochiq dataset importi (``scripts/import_public_dataset.py``) uchun testlar.

Tarmoqqa chiqilmaydi: faqat label matnini o‘girish, kadr tanlash va barqaror
fayl nomlari sinaladi. Har bir xato bu yerda ushlansa, `dataset/` ga noto‘g‘ri
sinf raqami bilan kadr tushmaydi.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import import_public_dataset as importer  # noqa: E402
import train_yolo  # noqa: E402

MOUSE_SRC = 9
LAPTOP_SRC = 8
KEYBOARD_SRC = 10
PEN_SRC = 0


def _line(class_id: int, cx=0.5, cy=0.5, w=0.2, h=0.2) -> str:
    return f"{class_id} {cx} {cy} {w} {h}"


# -- xaritalash -----------------------------------------------------------------
def test_every_mapped_code_exists_in_project_classes():
    for code in importer.CLASS_MAP.values():
        assert code in importer.CLASS_NAMES


def test_mapped_codes_have_the_same_index_as_data_yaml():
    # Sinf raqami data.yaml tartibiga tayangan — adashsa label noto‘g‘ri bo‘ladi.
    assert importer.CLASS_INDEX["mouse"] == 6
    assert importer.CLASS_INDEX["keyboard"] == 5
    assert importer.CLASS_INDEX["laptop"] == 4
    assert importer.CLASS_INDEX["monitor"] == 2
    assert importer.CLASS_INDEX["printer"] == 8
    assert importer.CLASS_INDEX["speaker"] == 11
    assert importer.CLASS_INDEX["smartphone"] == 18
    assert importer.CLASS_INDEX["tablet"] == 19


def test_index_to_code_round_trip():
    for code, index in importer.CLASS_INDEX.items():
        assert importer.INDEX_TO_CODE[index] == code


# -- label o‘girish -------------------------------------------------------------
def test_remap_rows_rewrites_class_ids_to_project_order():
    text = "\n".join([_line(MOUSE_SRC), _line(LAPTOP_SRC)])
    lines = importer.remap_rows(text)
    assert lines[0].startswith("6 ")
    assert lines[1].startswith("4 ")


def test_remap_rows_drops_classes_without_a_project_twin():
    lines = importer.remap_rows(_line(PEN_SRC))
    assert lines == []


def test_remap_rows_keeps_normalised_coordinates():
    lines = importer.remap_rows(_line(MOUSE_SRC, 0.25, 0.5, 0.1, 0.2))
    assert lines == ["6 0.250000 0.500000 0.100000 0.200000"]


def test_remap_rows_rejects_pixel_coordinates():
    # Normallashtirilmagan label (masalan 320 240 40 40) datasetni buzadi.
    assert importer.remap_rows("9 320 240 40 40") == []


def test_remap_rows_rejects_broken_lines():
    text = "\n".join(["", "mouse 0.5 0.5 0.1 0.1", "9 0.5 0.5", "9 a b c d", "9 0.5 0.5 0 0.2"])
    assert importer.remap_rows(text) == []


def test_remap_rows_passes_keyboard_and_monitor():
    text = "\n".join([_line(KEYBOARD_SRC), _line(11)])
    lines = importer.remap_rows(text)
    assert [line.split()[0] for line in lines] == ["5", "2"]


def test_clutter_count_counts_boxes_that_are_dropped():
    text = "\n".join([_line(MOUSE_SRC), _line(PEN_SRC), _line(20)])
    assert importer.clutter_count(text) == 2


# -- fayl nomlari ---------------------------------------------------------------
def test_sample_name_is_stable_and_starts_with_code():
    first = importer.sample_name("dataset/train/labels/gt_100_img (140).txt", "mouse")
    second = importer.sample_name("dataset/train/labels/gt_100_img (140).txt", "mouse")
    assert first == second
    assert first.startswith("mouse__hfdesk_")


def test_sample_name_differs_per_source():
    left = importer.sample_name("dataset/train/labels/a.txt", "mouse")
    right = importer.sample_name("dataset/train/labels/b.txt", "mouse")
    assert left != right


def test_sample_name_is_understood_by_train_yolo_prefix():
    name = importer.sample_name("dataset/train/labels/x.txt", "keyboard")
    assert train_yolo.stem_class(name) == "keyboard"


# -- tanlash --------------------------------------------------------------------
def _candidate(source: str, codes, clutter: int = 0, split: str = "train"):
    return importer.Candidate(label_path=source, split=split, codes=tuple(codes), clutter=clutter)


def test_plan_selection_respects_the_limit():
    candidates = [_candidate(f"dataset/train/labels/m{i}.txt", ["mouse"]) for i in range(10)]
    chosen = importer.plan_selection(candidates, 4)
    assert len(chosen) == 4


def test_plan_selection_does_not_let_a_common_class_eat_the_quota():
    candidates = [_candidate(f"dataset/train/labels/m{i}.txt", ["mouse"]) for i in range(10)]
    candidates += [_candidate(f"dataset/train/labels/t{i}.txt", ["tablet"]) for i in range(3)]
    chosen = importer.plan_selection(candidates, 4)
    codes = {code for item in chosen for code in item.codes}
    assert codes == {"mouse", "tablet"}
    assert sum(1 for item in chosen if "tablet" in item.codes) == 3


def test_plan_selection_prefers_frames_with_cleaner_labels():
    dirty = [_candidate(f"dataset/train/labels/d{i}.txt", ["mouse"], clutter=5) for i in range(5)]
    clean = [_candidate(f"dataset/train/labels/c{i}.txt", ["mouse"], clutter=0) for i in range(3)]
    chosen = importer.plan_selection(dirty + clean, 3)
    assert all(item.clutter == 0 for item in chosen)


def test_plan_selection_takes_a_frame_only_once():
    shared = _candidate("dataset/train/labels/shared.txt", ["mouse", "keyboard"])
    chosen = importer.plan_selection([shared], 5)
    assert len(chosen) == 1


def test_plan_selection_filters_by_requested_classes():
    candidates = [
        _candidate("dataset/train/labels/m.txt", ["mouse"]),
        _candidate("dataset/train/labels/p.txt", ["printer"]),
    ]
    chosen = importer.plan_selection(candidates, 5, {"mouse"})
    assert [item.label_path for item in chosen] == ["dataset/train/labels/m.txt"]


def test_plan_selection_is_deterministic():
    candidates = [_candidate(f"dataset/train/labels/m{i}.txt", ["mouse"]) for i in range(6)]
    first = [item.label_path for item in importer.plan_selection(candidates, 3)]
    second = [item.label_path for item in importer.plan_selection(list(reversed(candidates)), 3)]
    assert first == second


@pytest.mark.parametrize("limit", [0, -3])
def test_plan_selection_never_accepts_a_zero_limit(limit):
    candidates = [_candidate("dataset/train/labels/m.txt", ["mouse"])]
    assert len(importer.plan_selection(candidates, limit)) == 1
