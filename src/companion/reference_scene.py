"""Versioned, repository-safe schema for aligned Human Reference scenes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import json
import math
import os
from pathlib import Path
import re
from typing import Any, Callable, TypeVar

from companion.reference_storage import (
    ReferenceStorageError,
    validate_managed_relative_path,
)


SCENE_SCHEMA_VERSION = 1

_SCENE_KINDS = {"coupled", "behavior", "voice"}
_SPEAKER_ROLES = {"reference", "interlocutor"}
_ANNOTATION_SOURCES = {"human", "automatic", "derived", "unknown"}
_MEDIA_KINDS = {"audio", "video", "subtitle", "transcript"}
_EDIT_STATUSES = {"continuous", "edited", "unknown"}
_SCRIPTEDNESS = {"spontaneous", "mixed", "scripted", "unknown"}
_AUDIO_QUALITIES = {"clean", "usable", "limited", "reject", "unknown"}
_SHA256 = re.compile(r"[0-9a-f]{64}")


class ReferenceSceneError(ValueError):
    """Raised when a scene record is incomplete, unsafe, or inconsistent."""


@dataclass(frozen=True)
class TimeSpan:
    """Source-relative half-open time span expressed in milliseconds."""

    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class SceneContext:
    setting: str
    activity: str
    relationship_context: str
    preceding_context: str


@dataclass(frozen=True)
class AtmosphereObservation:
    labels: tuple[str, ...]
    summary: str
    annotation_source: str
    confidence: float


@dataclass(frozen=True)
class AtmosphereTransition:
    before: AtmosphereObservation
    after: AtmosphereObservation


@dataclass(frozen=True)
class MediaAsset:
    asset_id: str
    kind: str
    media_type: str
    relative_path: str
    sha256: str | None


@dataclass(frozen=True)
class DeliveryFeatures:
    speech_rate_syllables_per_second: float | None
    pitch_median_hz: float | None
    pitch_range_semitones: float | None
    energy_dbfs: float | None
    pause_before_ms: int | None
    pause_after_ms: int | None


@dataclass(frozen=True)
class NonverbalEvent:
    event_id: str
    kind: str
    span: TimeSpan
    annotation_source: str
    confidence: float


@dataclass(frozen=True)
class DialogueTurn:
    turn_id: str
    speaker_ref: str
    speaker_role: str
    span: TimeSpan
    raw_transcript: str
    normalized_transcript: str
    media_asset_id: str
    dialogue_act: str
    delivery: DeliveryFeatures
    nonverbal_events: tuple[NonverbalEvent, ...]


@dataclass(frozen=True)
class SceneQuality:
    edit_status: str
    scriptedness: str
    audio_quality: str
    overlap_ratio: float


@dataclass(frozen=True)
class Annotation:
    annotation_id: str
    target_id: str
    task: str
    label: str
    annotation_source: str
    confidence: float
    evidence_spans: tuple[TimeSpan, ...]


@dataclass(frozen=True)
class SceneRecord:
    schema_version: int
    scene_id: str
    scene_kind: str
    candidate_id: str
    source_id: str
    source_date: str
    span: TimeSpan
    context: SceneContext
    atmosphere: AtmosphereTransition
    media_assets: tuple[MediaAsset, ...]
    turns: tuple[DialogueTurn, ...]
    quality: SceneQuality
    annotations: tuple[Annotation, ...]


def scene_record_from_dict(raw: dict[str, Any]) -> SceneRecord:
    """Parse and validate the public, shareable part of one aligned scene."""
    raw = _object(raw, "scene")
    _exact_fields(
        raw,
        {
            "schema_version",
            "scene_id",
            "scene_kind",
            "candidate_id",
            "source_id",
            "source_date",
            "span",
            "context",
            "atmosphere",
            "media_assets",
            "turns",
            "quality",
            "annotations",
        },
        "scene",
    )
    scene = SceneRecord(
        schema_version=_integer(raw["schema_version"], "schema_version"),
        scene_id=_text(raw["scene_id"], "scene_id"),
        scene_kind=_enum(raw["scene_kind"], _SCENE_KINDS, "scene_kind"),
        candidate_id=_text(raw["candidate_id"], "candidate_id"),
        source_id=_text(raw["source_id"], "source_id"),
        source_date=_source_date(raw["source_date"]),
        span=_span(raw["span"], "scene span"),
        context=_context(raw["context"]),
        atmosphere=_atmosphere(raw["atmosphere"]),
        media_assets=_items(raw["media_assets"], _media_asset, "media_assets"),
        turns=_items(raw["turns"], _turn, "turns"),
        quality=_quality(raw["quality"]),
        annotations=_items(raw["annotations"], _annotation, "annotations"),
    )
    _validate_scene(scene)
    return scene


def scene_record_to_dict(scene: SceneRecord) -> dict[str, Any]:
    """Serialize a validated scene without private identity or source locators."""
    payload = _scene_record_to_dict_unchecked(scene)
    scene_record_from_dict(payload)
    return payload


def _scene_record_to_dict_unchecked(scene: SceneRecord) -> dict[str, Any]:
    _validate_scene(scene)
    return {
        "schema_version": scene.schema_version,
        "scene_id": scene.scene_id,
        "scene_kind": scene.scene_kind,
        "candidate_id": scene.candidate_id,
        "source_id": scene.source_id,
        "source_date": scene.source_date,
        "span": _span_to_dict(scene.span),
        "context": {
            "setting": scene.context.setting,
            "activity": scene.context.activity,
            "relationship_context": scene.context.relationship_context,
            "preceding_context": scene.context.preceding_context,
        },
        "atmosphere": {
            "before": _observation_to_dict(scene.atmosphere.before),
            "after": _observation_to_dict(scene.atmosphere.after),
        },
        "media_assets": [
            {
                "asset_id": asset.asset_id,
                "kind": asset.kind,
                "media_type": asset.media_type,
                "relative_path": asset.relative_path,
                "sha256": asset.sha256,
            }
            for asset in scene.media_assets
        ],
        "turns": [_turn_to_dict(turn) for turn in scene.turns],
        "quality": {
            "edit_status": scene.quality.edit_status,
            "scriptedness": scene.quality.scriptedness,
            "audio_quality": scene.quality.audio_quality,
            "overlap_ratio": scene.quality.overlap_ratio,
        },
        "annotations": [
            {
                "annotation_id": annotation.annotation_id,
                "target_id": annotation.target_id,
                "task": annotation.task,
                "label": annotation.label,
                "annotation_source": annotation.annotation_source,
                "confidence": annotation.confidence,
                "evidence_spans": [
                    _span_to_dict(span) for span in annotation.evidence_spans
                ],
            }
            for annotation in scene.annotations
        ],
    }


def load_scene_record(path: Path) -> SceneRecord:
    """Load a regular JSON file and validate it as a scene record."""
    if path.is_symlink() or not path.is_file():
        raise ReferenceSceneError("scene file is missing or unsafe")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReferenceSceneError(f"could not read scene file: {error}") from error
    if not isinstance(raw, dict):
        raise ReferenceSceneError("scene file must contain a JSON object")
    return scene_record_from_dict(raw)


def dump_scene_record(path: Path, scene: SceneRecord) -> None:
    """Create a new private scene JSON file without overwriting existing data."""
    payload = scene_record_to_dict(scene)
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
    except OSError as error:
        raise ReferenceSceneError(f"could not create scene file: {error}") from error


def _context(raw: Any) -> SceneContext:
    raw = _object(raw, "context")
    _exact_fields(
        raw,
        {"setting", "activity", "relationship_context", "preceding_context"},
        "context",
    )
    return SceneContext(
        setting=_text(raw["setting"], "context.setting"),
        activity=_text(raw["activity"], "context.activity"),
        relationship_context=_text(
            raw["relationship_context"], "context.relationship_context"
        ),
        preceding_context=_text(raw["preceding_context"], "context.preceding_context"),
    )


def _atmosphere(raw: Any) -> AtmosphereTransition:
    raw = _object(raw, "atmosphere")
    _exact_fields(raw, {"before", "after"}, "atmosphere")
    return AtmosphereTransition(
        before=_observation(raw["before"], "atmosphere.before"),
        after=_observation(raw["after"], "atmosphere.after"),
    )


def _observation(raw: Any, label: str) -> AtmosphereObservation:
    raw = _object(raw, label)
    _exact_fields(raw, {"labels", "summary", "annotation_source", "confidence"}, label)
    labels = _text_list(raw["labels"], f"{label}.labels")
    if not labels:
        raise ReferenceSceneError(f"{label}.labels must not be empty")
    return AtmosphereObservation(
        labels=labels,
        summary=_text(raw["summary"], f"{label}.summary"),
        annotation_source=_enum(
            raw["annotation_source"], _ANNOTATION_SOURCES, f"{label}.annotation_source"
        ),
        confidence=_confidence(raw["confidence"], f"{label}.confidence"),
    )


def _media_asset(raw: Any) -> MediaAsset:
    raw = _object(raw, "media asset")
    _exact_fields(
        raw, {"asset_id", "kind", "media_type", "relative_path", "sha256"}, "media asset"
    )
    try:
        relative_path = validate_managed_relative_path(raw["relative_path"])
    except (ReferenceStorageError, TypeError) as error:
        raise ReferenceSceneError(f"invalid media path: {error}") from error
    checksum = raw["sha256"]
    if checksum is not None and (
        not isinstance(checksum, str) or _SHA256.fullmatch(checksum) is None
    ):
        raise ReferenceSceneError("media asset sha256 must be null or lowercase SHA-256")
    return MediaAsset(
        asset_id=_text(raw["asset_id"], "media asset.asset_id"),
        kind=_enum(raw["kind"], _MEDIA_KINDS, "media asset.kind"),
        media_type=_text(raw["media_type"], "media asset.media_type"),
        relative_path=relative_path,
        sha256=checksum,
    )


def _delivery(raw: Any) -> DeliveryFeatures:
    raw = _object(raw, "delivery")
    _exact_fields(
        raw,
        {
            "speech_rate_syllables_per_second",
            "pitch_median_hz",
            "pitch_range_semitones",
            "energy_dbfs",
            "pause_before_ms",
            "pause_after_ms",
        },
        "delivery",
    )
    return DeliveryFeatures(
        speech_rate_syllables_per_second=_optional_number(
            raw["speech_rate_syllables_per_second"],
            "delivery.speech_rate_syllables_per_second",
            minimum=0.0,
            strict=True,
        ),
        pitch_median_hz=_optional_number(
            raw["pitch_median_hz"], "delivery.pitch_median_hz", minimum=0.0, strict=True
        ),
        pitch_range_semitones=_optional_number(
            raw["pitch_range_semitones"],
            "delivery.pitch_range_semitones",
            minimum=0.0,
        ),
        energy_dbfs=_optional_number(raw["energy_dbfs"], "delivery.energy_dbfs"),
        pause_before_ms=_optional_nonnegative_integer(
            raw["pause_before_ms"], "delivery.pause_before_ms"
        ),
        pause_after_ms=_optional_nonnegative_integer(
            raw["pause_after_ms"], "delivery.pause_after_ms"
        ),
    )


def _event(raw: Any) -> NonverbalEvent:
    raw = _object(raw, "nonverbal event")
    _exact_fields(
        raw,
        {"event_id", "kind", "span", "annotation_source", "confidence"},
        "nonverbal event",
    )
    return NonverbalEvent(
        event_id=_text(raw["event_id"], "nonverbal event.event_id"),
        kind=_text(raw["kind"], "nonverbal event.kind"),
        span=_span(raw["span"], "nonverbal event.span"),
        annotation_source=_enum(
            raw["annotation_source"],
            _ANNOTATION_SOURCES,
            "nonverbal event.annotation_source",
        ),
        confidence=_confidence(raw["confidence"], "nonverbal event.confidence"),
    )


def _turn(raw: Any) -> DialogueTurn:
    raw = _object(raw, "turn")
    _exact_fields(
        raw,
        {
            "turn_id",
            "speaker_ref",
            "speaker_role",
            "span",
            "raw_transcript",
            "normalized_transcript",
            "media_asset_id",
            "dialogue_act",
            "delivery",
            "nonverbal_events",
        },
        "turn",
    )
    return DialogueTurn(
        turn_id=_text(raw["turn_id"], "turn.turn_id"),
        speaker_ref=_text(raw["speaker_ref"], "turn.speaker_ref"),
        speaker_role=_enum(raw["speaker_role"], _SPEAKER_ROLES, "turn.speaker_role"),
        span=_span(raw["span"], "turn.span"),
        raw_transcript=_text(raw["raw_transcript"], "turn.raw_transcript"),
        normalized_transcript=_text(
            raw["normalized_transcript"], "turn.normalized_transcript"
        ),
        media_asset_id=_text(raw["media_asset_id"], "turn.media_asset_id"),
        dialogue_act=_text(raw["dialogue_act"], "turn.dialogue_act"),
        delivery=_delivery(raw["delivery"]),
        nonverbal_events=_items(
            raw["nonverbal_events"], _event, "turn.nonverbal_events"
        ),
    )


def _quality(raw: Any) -> SceneQuality:
    raw = _object(raw, "quality")
    _exact_fields(
        raw, {"edit_status", "scriptedness", "audio_quality", "overlap_ratio"}, "quality"
    )
    return SceneQuality(
        edit_status=_enum(raw["edit_status"], _EDIT_STATUSES, "quality.edit_status"),
        scriptedness=_enum(raw["scriptedness"], _SCRIPTEDNESS, "quality.scriptedness"),
        audio_quality=_enum(
            raw["audio_quality"], _AUDIO_QUALITIES, "quality.audio_quality"
        ),
        overlap_ratio=_bounded_number(raw["overlap_ratio"], "quality.overlap_ratio"),
    )


def _annotation(raw: Any) -> Annotation:
    raw = _object(raw, "annotation")
    _exact_fields(
        raw,
        {
            "annotation_id",
            "target_id",
            "task",
            "label",
            "annotation_source",
            "confidence",
            "evidence_spans",
        },
        "annotation",
    )
    return Annotation(
        annotation_id=_text(raw["annotation_id"], "annotation.annotation_id"),
        target_id=_text(raw["target_id"], "annotation.target_id"),
        task=_text(raw["task"], "annotation.task"),
        label=_text(raw["label"], "annotation.label"),
        annotation_source=_enum(
            raw["annotation_source"], _ANNOTATION_SOURCES, "annotation.annotation_source"
        ),
        confidence=_confidence(raw["confidence"], "annotation.confidence"),
        evidence_spans=_items(
            raw["evidence_spans"],
            lambda value: _span(value, "annotation.evidence_span"),
            "annotation.evidence_spans",
        ),
    )


def _validate_scene(scene: SceneRecord) -> None:
    if scene.schema_version != SCENE_SCHEMA_VERSION:
        raise ReferenceSceneError(
            f"unsupported scene schema version: {scene.schema_version!r}"
        )

    assets = _unique_by(
        scene.media_assets, lambda asset: asset.asset_id, "duplicate media asset ID"
    )
    turns = _unique_by(scene.turns, lambda turn: turn.turn_id, "duplicate turn ID")
    annotations = _unique_by(
        scene.annotations,
        lambda annotation: annotation.annotation_id,
        "duplicate annotation ID",
    )

    for asset in scene.media_assets:
        try:
            normalized = validate_managed_relative_path(asset.relative_path)
        except ReferenceStorageError as error:
            raise ReferenceSceneError(f"invalid media path: {error}") from error
        if normalized != asset.relative_path:
            raise ReferenceSceneError("media path must use normalized POSIX form")

    event_ids: set[str] = set()
    for turn in scene.turns:
        if not _contains_span(scene.span, turn.span):
            raise ReferenceSceneError("every turn span must be inside scene span")
        asset = assets.get(turn.media_asset_id)
        if asset is None:
            raise ReferenceSceneError(
                f"turn {turn.turn_id!r} references an unknown media asset"
            )
        if asset.kind not in {"audio", "video"}:
            raise ReferenceSceneError("turn media asset must be audio or video")
        for event in turn.nonverbal_events:
            if event.event_id in event_ids:
                raise ReferenceSceneError("duplicate nonverbal event ID")
            event_ids.add(event.event_id)
            if not _contains_span(turn.span, event.span):
                raise ReferenceSceneError(
                    "every nonverbal event span must be inside owning turn"
                )

    if scene.scene_kind == "coupled":
        roles = {turn.speaker_role for turn in scene.turns}
        if roles != _SPEAKER_ROLES:
            raise ReferenceSceneError(
                "coupled scene must contain reference and interlocutor turns"
            )

    known_targets = {scene.scene_id, *assets.keys(), *turns.keys()}
    for annotation in annotations.values():
        if annotation.target_id not in known_targets:
            raise ReferenceSceneError(
                f"annotation references an unknown target: {annotation.target_id!r}"
            )
        for evidence_span in annotation.evidence_spans:
            if not _contains_span(scene.span, evidence_span):
                raise ReferenceSceneError(
                    "annotation evidence span must be inside scene span"
                )


def _turn_to_dict(turn: DialogueTurn) -> dict[str, Any]:
    return {
        "turn_id": turn.turn_id,
        "speaker_ref": turn.speaker_ref,
        "speaker_role": turn.speaker_role,
        "span": _span_to_dict(turn.span),
        "raw_transcript": turn.raw_transcript,
        "normalized_transcript": turn.normalized_transcript,
        "media_asset_id": turn.media_asset_id,
        "dialogue_act": turn.dialogue_act,
        "delivery": {
            "speech_rate_syllables_per_second": (
                turn.delivery.speech_rate_syllables_per_second
            ),
            "pitch_median_hz": turn.delivery.pitch_median_hz,
            "pitch_range_semitones": turn.delivery.pitch_range_semitones,
            "energy_dbfs": turn.delivery.energy_dbfs,
            "pause_before_ms": turn.delivery.pause_before_ms,
            "pause_after_ms": turn.delivery.pause_after_ms,
        },
        "nonverbal_events": [
            {
                "event_id": event.event_id,
                "kind": event.kind,
                "span": _span_to_dict(event.span),
                "annotation_source": event.annotation_source,
                "confidence": event.confidence,
            }
            for event in turn.nonverbal_events
        ],
    }


def _observation_to_dict(observation: AtmosphereObservation) -> dict[str, Any]:
    return {
        "labels": list(observation.labels),
        "summary": observation.summary,
        "annotation_source": observation.annotation_source,
        "confidence": observation.confidence,
    }


def _span(raw: Any, label: str) -> TimeSpan:
    raw = _object(raw, label)
    _exact_fields(raw, {"start_ms", "end_ms"}, label)
    span = TimeSpan(
        start_ms=_integer(raw["start_ms"], f"{label}.start_ms"),
        end_ms=_integer(raw["end_ms"], f"{label}.end_ms"),
    )
    if span.start_ms < 0 or span.end_ms <= span.start_ms:
        raise ReferenceSceneError(
            f"{label} must satisfy 0 <= start_ms < end_ms"
        )
    return span


def _span_to_dict(span: TimeSpan) -> dict[str, int]:
    return {"start_ms": span.start_ms, "end_ms": span.end_ms}


def _contains_span(parent: TimeSpan, child: TimeSpan) -> bool:
    return parent.start_ms <= child.start_ms and child.end_ms <= parent.end_ms


def _object(raw: Any, label: str) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ReferenceSceneError(f"{label} must be an object")
    return raw


def _exact_fields(raw: dict[str, Any], expected: set[str], label: str) -> None:
    actual = set(raw)
    unexpected = sorted(actual - expected)
    missing = sorted(expected - actual)
    if unexpected:
        raise ReferenceSceneError(f"{label} has unexpected fields: {unexpected}")
    if missing:
        raise ReferenceSceneError(f"{label} is missing required fields: {missing}")


def _text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceSceneError(f"{label} must be a non-empty string")
    return value


def _text_list(value: Any, label: str) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ReferenceSceneError(f"{label} must be a list")
    return tuple(_text(item, label) for item in value)


def _enum(value: Any, choices: set[str], label: str) -> str:
    value = _text(value, label)
    if value not in choices:
        raise ReferenceSceneError(f"{label} must be one of {sorted(choices)}")
    return value


def _integer(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReferenceSceneError(f"{label} must be an integer")
    return value


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReferenceSceneError(f"{label} must be a finite number")
    converted = float(value)
    if not math.isfinite(converted):
        raise ReferenceSceneError(f"{label} must be a finite number")
    return converted


def _bounded_number(value: Any, label: str) -> float:
    converted = _number(value, label)
    if not 0.0 <= converted <= 1.0:
        raise ReferenceSceneError(f"{label} must be between 0 and 1")
    return converted


def _confidence(value: Any, label: str) -> float:
    return _bounded_number(value, label)


def _optional_number(
    value: Any,
    label: str,
    *,
    minimum: float | None = None,
    strict: bool = False,
) -> float | None:
    if value is None:
        return None
    converted = _number(value, label)
    if minimum is not None and (
        converted < minimum or (strict and converted == minimum)
    ):
        comparison = "greater than" if strict else "at least"
        raise ReferenceSceneError(f"{label} must be {comparison} {minimum}")
    return converted


def _optional_nonnegative_integer(value: Any, label: str) -> int | None:
    if value is None:
        return None
    converted = _integer(value, label)
    if converted < 0:
        raise ReferenceSceneError(f"{label} must be non-negative")
    return converted


def _source_date(value: Any) -> str:
    value = _text(value, "source_date")
    try:
        parsed = date.fromisoformat(value)
    except ValueError as error:
        raise ReferenceSceneError("source_date must use ISO YYYY-MM-DD form") from error
    if parsed.isoformat() != value:
        raise ReferenceSceneError("source_date must use ISO YYYY-MM-DD form")
    return value


T = TypeVar("T")


def _items(raw: Any, parser: Callable[[Any], T], label: str) -> tuple[T, ...]:
    if not isinstance(raw, list):
        raise ReferenceSceneError(f"{label} must be a list")
    return tuple(parser(item) for item in raw)


def _unique_by(
    items: tuple[T, ...], key: Callable[[T], str], error_message: str
) -> dict[str, T]:
    result: dict[str, T] = {}
    for item in items:
        item_key = key(item)
        if item_key in result:
            raise ReferenceSceneError(error_message)
        result[item_key] = item
    return result
