"""Person <-> equipment association must express uncertainty, never guess."""

from __future__ import annotations

import pytest

from app.models.enums import AssociationQuality
from app.services.association_service import AssociationConfig, AssociationEngine, object_near_person
from app.services.types import BBox, TrackFeature


def person(track_id: int = 1, bbox: tuple = (100, 100, 200, 400), frames: int = 20, velocity=(0, 0)):
    return TrackFeature(
        track_id=track_id,
        bbox=BBox(*bbox),
        is_person=True,
        category="person",
        confidence=0.92,
        frames_seen=frames,
        velocity=velocity,
    )


def equipment(track_id: int = 2, bbox: tuple = (120, 220, 190, 280), frames: int = 20, velocity=(0, 0)):
    return TrackFeature(
        track_id=track_id,
        bbox=BBox(*bbox),
        is_person=False,
        category="laptop",
        confidence=0.80,
        frames_seen=frames,
        velocity=velocity,
    )


def test_object_inside_body_is_strong_association():
    engine = AssociationEngine()
    result = engine.associate([person()], [equipment()])[0]
    assert result.person_track_id == 1
    assert result.quality is AssociationQuality.STRONG
    assert result.requires_review is False
    assert result.score >= AssociationConfig().strong_score


def test_two_equally_close_people_are_ambiguous():
    engine = AssociationEngine()
    left = person(1, (100, 100, 200, 400), velocity=(1, 0))
    right = person(2, (100, 100, 200, 400), velocity=(1, 0))
    result = engine.associate([left, right], [equipment()])[0]
    assert result.quality is AssociationQuality.AMBIGUOUS
    assert result.requires_review is True
    assert any("noaniq" in reason for reason in result.reasons)


def test_far_away_object_is_not_attached_to_anybody():
    engine = AssociationEngine()
    distant = equipment(bbox=(900, 600, 980, 660))
    result = engine.associate([person()], [distant])[0]
    assert result.person_track_id is None
    assert result.quality is AssociationQuality.NONE
    assert result.score == 0.0


def test_short_track_requires_review():
    engine = AssociationEngine()
    result = engine.associate([person(frames=2)], [equipment(frames=2)])[0]
    assert result.requires_review is True
    assert result.quality in {AssociationQuality.WEAK, AssociationQuality.AMBIGUOUS}


def test_direction_agreement_increases_score():
    engine = AssociationEngine()
    same_direction = engine.associate(
        [person(velocity=(5, 1))], [equipment(velocity=(5, 1))]
    )[0]
    opposite = engine.associate(
        [person(velocity=(5, 1))], [equipment(velocity=(-5, -1))]
    )[0]
    assert same_direction.score > opposite.score
    assert any("yo‘nalishi mos emas" in reason for reason in opposite.reasons)


def test_multiple_objects_get_independent_results():
    engine = AssociationEngine()
    results = engine.associate(
        [person()],
        [equipment(2, (120, 220, 190, 280)), equipment(3, (1500, 1500, 1600, 1600))],
    )
    by_track = {item.object_track_id: item for item in results}
    assert by_track[2].quality is AssociationQuality.STRONG
    assert by_track[3].quality is AssociationQuality.NONE


@pytest.mark.parametrize("ratio", [0.5, 1.0, 1.5])
def test_object_near_person_helper(ratio):
    assert object_near_person(person(), equipment(), ratio) is True
    assert object_near_person(person(), equipment(bbox=(2000, 2000, 2100, 2100)), ratio) is False


def test_empty_inputs_return_no_evidence():
    engine = AssociationEngine()
    assert engine.associate([], [equipment()])[0].quality is AssociationQuality.NONE
    assert engine.associate([person()], []) == []
