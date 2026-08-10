"""Score transcript segments against an enrolled Reference voice.

Runs inside the pinned speaker-embedding image. Takes a 16 kHz mono WAV (the
same one the transcription probe produces) plus its VTT, embeds every cue window
and reports how close each is to the enrolment voice.

Only numbers are printed. Segment text is never read here — the VTT is used for
its timings alone, so no transcript content can leak into a log.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import shutil

from speechbrain.inference.speaker import EncoderClassifier
import torch
import torchaudio

from companion.reference_subtitle_probe import SubtitleCue, parse_webvtt
from companion.speaker_verification import (
    MINIMUM_RELIABLE_SECONDS,
    SegmentScore,
    SpeakerVerificationError,
    cosine_similarity,
    positive_control_rate,
    public_verification_summary,
    verify_segments,
)

EXPECTED_SAMPLE_RATE = 16000


def load_encoder(model_dir: Path) -> EncoderClassifier:
    """Load the baked checkpoint without writing into the image.

    speechbrain links its hyperparameters from ``source`` into ``savedir`` and
    then opens them for writing. With a read-only container root that write
    lands on the baked file and fails, so the checkpoint is copied into the
    tmpfs first and both paths point there.
    """
    scratch = Path("/tmp/speaker-model")
    if not scratch.exists():
        shutil.copytree(model_dir, scratch)
    # The Hub cache moves too. Offline resolution reads it, and huggingface_hub
    # still expects to be able to write lock files beside what it reads.
    baked_cache = Path(os.environ.get("SPEAKER_HF_CACHE", "/opt/hf-cache"))
    cache = Path("/tmp/hf-cache")
    if baked_cache.is_dir() and not cache.exists():
        shutil.copytree(baked_cache, cache)
    os.environ["HF_HOME"] = str(cache)
    return EncoderClassifier.from_hparams(source=str(scratch), savedir=str(scratch))


def load_audio(path: Path) -> torch.Tensor:
    waveform, sample_rate = torchaudio.load(str(path))
    if sample_rate != EXPECTED_SAMPLE_RATE:
        raise SpeakerVerificationError(
            f"audio must be {EXPECTED_SAMPLE_RATE} Hz, got {sample_rate}"
        )
    if waveform.shape[0] != 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    return waveform


def embed(encoder: EncoderClassifier, waveform: torch.Tensor) -> tuple[float, ...]:
    with torch.no_grad():
        embedding = encoder.encode_batch(waveform)
    return tuple(embedding.squeeze().tolist())


def _window(waveform: torch.Tensor, start: float, end: float) -> torch.Tensor | None:
    first = int(start * EXPECTED_SAMPLE_RATE)
    last = min(int(end * EXPECTED_SAMPLE_RATE), waveform.shape[1])
    if last - first < int(0.4 * EXPECTED_SAMPLE_RATE):
        return None
    return waveform[:, first:last]


def enrolment_embedding(
    encoder: EncoderClassifier,
    waveform: torch.Tensor,
    cues: tuple[SubtitleCue, ...],
    *,
    limit: int,
) -> tuple[float, ...]:
    """Average the longest cue windows.

    Longest first because a short window carries more room and less voice, and
    the enrolment vector is the one error every later score inherits.
    """
    ordered = sorted(cues, key=lambda cue: cue.end_seconds - cue.start_seconds, reverse=True)
    vectors: list[tuple[float, ...]] = []
    for cue in ordered:
        if len(vectors) >= limit:
            break
        window = _window(waveform, cue.start_seconds, cue.end_seconds)
        if window is None:
            continue
        vectors.append(embed(encoder, window))
    if not vectors:
        raise SpeakerVerificationError("no usable enrolment windows")
    # Normalise before averaging. Cosine similarity only sees direction, so a
    # raw mean lets loud windows pull the centroid by magnitude alone.
    unit = [_unit(vector) for vector in vectors]
    dimension = len(unit[0])
    return _unit(
        tuple(
            sum(vector[index] for vector in unit) / len(unit) for index in range(dimension)
        )
    )


def _unit(vector: tuple[float, ...]) -> tuple[float, ...]:
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        raise SpeakerVerificationError("embedding has zero magnitude")
    return tuple(value / norm for value in vector)


def score_segments(
    encoder: EncoderClassifier,
    waveform: torch.Tensor,
    cues: tuple[SubtitleCue, ...],
    enrolment: tuple[float, ...],
) -> tuple[SegmentScore, ...]:
    scores: list[SegmentScore] = []
    for index, cue in enumerate(cues):
        window = _window(waveform, cue.start_seconds, cue.end_seconds)
        if window is None:
            continue
        scores.append(
            SegmentScore(
                index=index,
                start_seconds=cue.start_seconds,
                end_seconds=cue.end_seconds,
                similarity=cosine_similarity(embed(encoder, window), enrolment),
            )
        )
    if not scores:
        raise SpeakerVerificationError("no usable segments to score")
    return tuple(scores)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enrolment-audio", type=Path, required=True)
    parser.add_argument("--enrolment-vtt", type=Path, required=True)
    parser.add_argument("--enrolment-ratio", type=float, default=0.7)
    parser.add_argument("--enrolment-windows", type=int, default=40)
    parser.add_argument("--target-audio", type=Path, required=True)
    parser.add_argument("--target-vtt", type=Path, required=True)
    parser.add_argument("--target-source-id", required=True)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path(os.environ.get("SPEAKER_MODEL_DIR", "/opt/speaker-model")),
    )
    parser.add_argument("--report-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        encoder = load_encoder(arguments.model_dir)

        enrolment_cues = parse_webvtt(arguments.enrolment_vtt.read_text(encoding="utf-8"))
        enrolment_audio = load_audio(arguments.enrolment_audio)
        ordered = sorted(enrolment_cues, key=lambda cue: cue.start_seconds)
        cut = int(len(ordered) * arguments.enrolment_ratio)
        if cut < 1 or cut >= len(ordered):
            raise SpeakerVerificationError("enrolment split left one side empty")
        train_cues, control_cues = tuple(ordered[:cut]), tuple(ordered[cut:])

        enrolment = enrolment_embedding(
            encoder, enrolment_audio, train_cues, limit=arguments.enrolment_windows
        )

        # The held-out part of the single-speaker source: every one of these is
        # the Reference, so a low acceptance rate condemns the method itself.
        control_scores = score_segments(encoder, enrolment_audio, control_cues, enrolment)

        target_audio = load_audio(arguments.target_audio)
        target_cues = parse_webvtt(arguments.target_vtt.read_text(encoding="utf-8"))
        target_scores = score_segments(encoder, target_audio, target_cues, enrolment)

        target_report = verify_segments(arguments.target_source_id, target_scores)
        control_report = verify_segments("enrolment-control", control_scores)
        threshold = target_report.separation.threshold
        payload: dict[str, object] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "minimum_reliable_seconds": MINIMUM_RELIABLE_SECONDS,
            "enrolment": {
                "train_segments": len(train_cues),
                "control_segments": len(control_cues),
                "windows_used": min(arguments.enrolment_windows, len(train_cues)),
            },
            "positive_control": public_verification_summary(control_report),
            "target": public_verification_summary(target_report),
            "positive_control_rate_at_target_threshold": (
                None
                if threshold is None
                else round(positive_control_rate(control_scores, threshold), 4)
            ),
        }
    except (SpeakerVerificationError, OSError, RuntimeError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    if arguments.report_path:
        descriptor = os.open(
            arguments.report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")

    payload["status"] = "ok"
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
