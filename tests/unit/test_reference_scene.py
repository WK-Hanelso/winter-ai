from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import pytest

from companion.reference_scene import (
    SCENE_SCHEMA_VERSION,
    ReferenceSceneError,
    dump_scene_record,
    load_scene_record,
    scene_record_from_dict,
    scene_record_to_dict,
)


FIXTURE_PATH = Path(__file__).parents[1] / "fixtures" / "reference_scene_v1.json"


def _fixture_dict() -> dict[str, object]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def test_synthetic_scene_fixture_round_trip_is_lossless(tmp_path: Path) -> None:
    raw = _fixture_dict()
    scene = scene_record_from_dict(raw)

    assert scene.schema_version == SCENE_SCHEMA_VERSION
    assert scene.turns[0].delivery.pitch_median_hz is None
    assert scene.turns[1].raw_transcript.startswith("음...")
    assert scene.turns[1].normalized_transcript.startswith("그럼")
    assert scene_record_to_dict(scene) == raw

    output = tmp_path / "scene.json"
    dump_scene_record(output, scene)
    assert load_scene_record(output) == scene


def test_scene_dump_refuses_to_overwrite_existing_file(tmp_path: Path) -> None:
    scene = scene_record_from_dict(_fixture_dict())
    output = tmp_path / "scene.json"

    dump_scene_record(output, scene)
    with pytest.raises(ReferenceSceneError, match="could not create scene file"):
        dump_scene_record(output, scene)


def test_scene_serialization_revalidates_direct_dataclass_changes() -> None:
    scene = scene_record_from_dict(_fixture_dict())
    invalid = replace(
        scene,
        quality=replace(scene.quality, edit_status="probably-continuous"),
    )

    with pytest.raises(ReferenceSceneError, match="edit_status"):
        scene_record_to_dict(invalid)


def test_scene_rejects_turn_outside_scene_span() -> None:
    raw = _fixture_dict()
    raw["turns"][1]["span"]["end_ms"] = 140000

    with pytest.raises(ReferenceSceneError, match="inside scene span"):
        scene_record_from_dict(raw)


@pytest.mark.parametrize("confidence", (-0.1, 1.1))
def test_scene_rejects_invalid_confidence(confidence: float) -> None:
    raw = _fixture_dict()
    raw["atmosphere"]["before"]["confidence"] = confidence

    with pytest.raises(ReferenceSceneError, match="confidence"):
        scene_record_from_dict(raw)


def test_scene_rejects_invalid_quality_enum() -> None:
    raw = _fixture_dict()
    raw["quality"]["edit_status"] = "probably-continuous"

    with pytest.raises(ReferenceSceneError, match="edit_status"):
        scene_record_from_dict(raw)


def test_scene_rejects_missing_media_reference() -> None:
    raw = _fixture_dict()
    raw["turns"][1]["media_asset_id"] = "missing-audio"

    with pytest.raises(ReferenceSceneError, match="unknown media asset"):
        scene_record_from_dict(raw)


def test_scene_rejects_missing_annotation_target() -> None:
    raw = _fixture_dict()
    raw["annotations"][0]["target_id"] = "missing-turn"

    with pytest.raises(ReferenceSceneError, match="unknown target"):
        scene_record_from_dict(raw)


@pytest.mark.parametrize(
    "unsafe_path",
    ("../outside/audio.wav", "/tmp/outside.wav", "unknown/audio.wav"),
)
def test_scene_rejects_unsafe_media_path(unsafe_path: str) -> None:
    raw = _fixture_dict()
    raw["media_assets"][0]["relative_path"] = unsafe_path

    with pytest.raises(ReferenceSceneError, match="media path"):
        scene_record_from_dict(raw)


def test_scene_rejects_private_or_unknown_fields() -> None:
    raw = _fixture_dict()
    raw["private_source_uri"] = "private://must-not-appear"

    with pytest.raises(ReferenceSceneError, match="unexpected fields"):
        scene_record_from_dict(raw)


def test_scene_rejects_event_outside_owning_turn() -> None:
    raw = _fixture_dict()
    raw["turns"][1]["nonverbal_events"][0]["span"]["start_ms"] = 123000

    with pytest.raises(ReferenceSceneError, match="inside owning turn"):
        scene_record_from_dict(raw)


def test_scene_rejects_duplicate_cross_references() -> None:
    raw = _fixture_dict()
    duplicate = deepcopy(raw["turns"][0])
    raw["turns"].append(duplicate)

    with pytest.raises(ReferenceSceneError, match="duplicate turn ID"):
        scene_record_from_dict(raw)
