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

from companion.diarization import SpeakerSpan, parse_spans
from companion.reference_subtitle_probe import SubtitleCue, parse_webvtt
from companion.speaker_verification import (
    DEFAULT_HOP_SECONDS,
    DEFAULT_WINDOW_SECONDS,
    MINIMUM_RELIABLE_SECONDS,
    SegmentScore,
    SpeakerVerificationError,
    audible_cues,
    cosine_similarity,
    identify_reference_speaker,
    iter_windows,
    label_clusters,
    positive_control_rate,
    public_cluster_summary,
    public_identification_summary,
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


def window_embeddings(
    encoder: EncoderClassifier,
    waveform: torch.Tensor,
    cues: tuple[SubtitleCue, ...],
    *,
    window_seconds: float,
    hop_seconds: float,
) -> tuple[tuple[float, ...], ...]:
    vectors: list[tuple[float, ...]] = []
    for cue in audible_cues(cues):
        for start, end in iter_windows(
            cue.start_seconds,
            cue.end_seconds,
            window_seconds=window_seconds,
            hop_seconds=hop_seconds,
        ):
            window = _window(waveform, start, end)
            if window is not None:
                vectors.append(embed(encoder, window))
    if not vectors:
        raise SpeakerVerificationError("no usable windows to embed")
    return tuple(vectors)


def score_segments(
    encoder: EncoderClassifier,
    waveform: torch.Tensor,
    cues: tuple[SubtitleCue, ...],
    enrolment: tuple[float, ...],
    *,
    by_window: bool,
    window_seconds: float = DEFAULT_WINDOW_SECONDS,
    hop_seconds: float = DEFAULT_HOP_SECONDS,
) -> tuple[SegmentScore, ...]:
    """Score either whole cues or windows cut inside them.

    Cue-level scoring is kept so the two can be compared on the same audio; it
    was the approach that failed, and the comparison is the evidence.
    """
    scores: list[SegmentScore] = []
    for index, cue in enumerate(audible_cues(cues)):
        spans = (
            iter_windows(
                cue.start_seconds,
                cue.end_seconds,
                window_seconds=window_seconds,
                hop_seconds=hop_seconds,
            )
            if by_window
            else ((cue.start_seconds, cue.end_seconds),)
        )
        for start, end in spans:
            window = _window(waveform, start, end)
            if window is None:
                continue
            scores.append(
                SegmentScore(
                    index=index,
                    start_seconds=start,
                    end_seconds=end,
                    similarity=cosine_similarity(embed(encoder, window), enrolment),
                )
            )
    if not scores:
        raise SpeakerVerificationError("no usable segments to score")
    return tuple(scores)


def speaker_centroids(
    encoder: EncoderClassifier,
    waveform: torch.Tensor,
    spans: tuple[SpeakerSpan, ...],
    *,
    window_seconds: float,
    hop_seconds: float,
) -> tuple[dict[int, tuple[float, ...]], dict[int, float]]:
    """One averaged voice vector per diarized speaker, plus how long each spoke."""
    collected: dict[int, list[tuple[float, ...]]] = {}
    seconds: dict[int, float] = {}
    for span in spans:
        seconds[span.speaker] = seconds.get(span.speaker, 0.0) + span.duration_seconds
        for start, end in iter_windows(
            span.start_seconds,
            span.end_seconds,
            window_seconds=window_seconds,
            hop_seconds=hop_seconds,
        ):
            window = _window(waveform, start, end)
            if window is not None:
                collected.setdefault(span.speaker, []).append(_unit(embed(encoder, window)))
    if not collected:
        raise SpeakerVerificationError("no usable audio for any diarized speaker")
    centroids: dict[int, tuple[float, ...]] = {}
    for speaker, vectors in collected.items():
        dimension = len(vectors[0])
        centroids[speaker] = _unit(
            tuple(
                sum(vector[index] for vector in vectors) / len(vectors)
                for index in range(dimension)
            )
        )
    return centroids, seconds


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enrolment-audio", type=Path, required=True)
    parser.add_argument("--enrolment-vtt", type=Path, required=True)
    parser.add_argument("--enrolment-ratio", type=float, default=0.7)
    parser.add_argument("--enrolment-windows", type=int, default=40)
    parser.add_argument("--target-audio", type=Path, required=True)
    parser.add_argument("--target-vtt", type=Path, required=True)
    parser.add_argument("--target-source-id", required=True)
    parser.add_argument("--window-seconds", type=float, default=DEFAULT_WINDOW_SECONDS)
    parser.add_argument("--hop-seconds", type=float, default=DEFAULT_HOP_SECONDS)
    parser.add_argument(
        "--model-dir",
        type=Path,
        default=Path(os.environ.get("SPEAKER_MODEL_DIR", "/opt/speaker-model")),
    )
    parser.add_argument("--report-path", type=Path)
    parser.add_argument(
        "--spans-path",
        type=Path,
        help="speaker timeline from the diarization probe; enables speaker identification",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        encoder = load_encoder(arguments.model_dir)

        enrolment_cues = parse_webvtt(arguments.enrolment_vtt.read_text(encoding="utf-8"))
        enrolment_audio = load_audio(arguments.enrolment_audio)
        ordered = sorted(audible_cues(enrolment_cues), key=lambda cue: cue.start_seconds)
        cut = int(len(ordered) * arguments.enrolment_ratio)
        if cut < 1 or cut >= len(ordered):
            raise SpeakerVerificationError("enrolment split left one side empty")
        train_cues, control_cues = tuple(ordered[:cut]), tuple(ordered[cut:])

        enrolment = enrolment_embedding(
            encoder, enrolment_audio, train_cues, limit=arguments.enrolment_windows
        )

        # The held-out part of the single-speaker source: every one of these is
        # the Reference, so a low acceptance rate condemns the method itself.
        target_audio = load_audio(arguments.target_audio)
        target_cues = parse_webvtt(arguments.target_vtt.read_text(encoding="utf-8"))

        def measure(by_window: bool) -> dict[str, object]:
            control = score_segments(
                encoder,
                enrolment_audio,
                control_cues,
                enrolment,
                by_window=by_window,
                window_seconds=arguments.window_seconds,
                hop_seconds=arguments.hop_seconds,
            )
            target = score_segments(
                encoder,
                target_audio,
                target_cues,
                enrolment,
                by_window=by_window,
                window_seconds=arguments.window_seconds,
                hop_seconds=arguments.hop_seconds,
            )
            target_report = verify_segments(arguments.target_source_id, target)
            threshold = target_report.separation.threshold
            return {
                "positive_control": public_verification_summary(
                    verify_segments("enrolment-control", control)
                ),
                "target": public_verification_summary(target_report),
                "positive_control_rate_at_target_threshold": (
                    None
                    if threshold is None
                    else round(positive_control_rate(control, threshold), 4)
                ),
            }

        payload: dict[str, object] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "minimum_reliable_seconds": MINIMUM_RELIABLE_SECONDS,
            "window_seconds": arguments.window_seconds,
            "hop_seconds": arguments.hop_seconds,
            "enrolment": {
                "train_segments": len(train_cues),
                "control_segments": len(control_cues),
                "windows_used": min(arguments.enrolment_windows, len(train_cues)),
            },
            # Both are reported: the cue-level run is the approach that failed,
            # and keeping it makes the comparison the evidence.
            "by_cue": measure(by_window=False),
            "by_window": measure(by_window=True),
        }

        # Absolute similarity collapses across recordings. Clustering inside the
        # target removes that shift; the enrolment then only names a group.
        target_vectors = window_embeddings(
            encoder,
            target_audio,
            target_cues,
            window_seconds=arguments.window_seconds,
            hop_seconds=arguments.hop_seconds,
        )
        if arguments.spans_path:
            raw_spans = json.loads(arguments.spans_path.read_text(encoding="utf-8"))
            spans = parse_spans(
                tuple((row[0], row[1], int(row[2])) for row in raw_spans["spans"])
            )
            centroids, seconds = speaker_centroids(
                encoder,
                target_audio,
                spans,
                window_seconds=arguments.window_seconds,
                hop_seconds=arguments.hop_seconds,
            )
            payload["identified_speaker"] = public_identification_summary(
                identify_reference_speaker(centroids, seconds, enrolment)
            )
            payload["speaker_seconds"] = {
                str(speaker): round(value, 2) for speaker, value in sorted(seconds.items())
            }

        payload["clustered"] = public_cluster_summary(
            label_clusters(target_vectors, enrolment)
        )
        control_vectors = window_embeddings(
            encoder,
            enrolment_audio,
            control_cues,
            window_seconds=arguments.window_seconds,
            hop_seconds=arguments.hop_seconds,
        )
        # Single-speaker audio must NOT split convincingly. If it does, the
        # clustering is finding something other than speakers.
        payload["clustered_control"] = public_cluster_summary(
            label_clusters(control_vectors, enrolment)
        )
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
