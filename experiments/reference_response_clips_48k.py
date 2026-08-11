"""Re-cut the Reference response clips from the original audio, at full rate.

The existing clips are 16 kHz because that is what diarization and transcription
wanted. The source is 48 kHz stereo Opus, so those clips threw away most of the
signal before anyone asked whether voice cloning needed it. This rebuilds them
from the source without redoing any of the alignment work.

Nothing here re-derives *which* spans are responses. That was settled by
diarization and speaker identification. This only finds where each existing clip
sits in the original recording and cuts the same span again at a higher rate.

Where the span comes from
-------------------------
Not from the pairs JSON. ``response_seconds`` there is the response's *duration*,
not its start — checked against every clip's length, 11 of 11 exact. No start
time was ever written to disk, so it cannot be read back.

So each clip is *located* instead of looked up: cross-correlate it against the
whole source and take the peak. That is measured against the very signal being
cut, so no offset is assumed anywhere.

An earlier version located clips inside the 300 s chunk named in the filename and
added the chunk's offset. That failed for 12 of 42 clips, all from one chunk:
``source-003-0-300-words.wav`` matches the source at 0 s and drifts apart after,
while every other chunk matches its stated offset for its full length. Whatever
produced that one chunk did not produce a straight copy. Going to the source
directly removes the assumption instead of special-casing the chunk.

Locating rather than looking up also makes the step self-checking: every output
is re-downsampled and correlated against the clip it should reproduce, and a
result below ``--minimum-correlation`` fails the run. A silently misaligned
training set would be very expensive to discover later, and correlation catches
it now.

Runtime
-------
Needs numpy and ffmpeg, so it runs in the pinned ``winter-ai:diarization`` image
rather than the dev container. That image is heavy for this job, but it already
exists and already has both; building a fourth image to cut audio would cost more
than it saves.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import wave

import numpy as np

# source-003-600-300-response-000.wav → source-003, chunk at 600 s, response 0
CLIP_NAME = re.compile(r"^(?P<source>.+?)-(?P<offset>\d+)-(?P<window>\d+)-response-(?P<index>\d+)$")
CHUNK_RATE = 16_000
TARGET_RATE = 48_000
# Mono: the Reference is one speaker and every cloning model wants one channel.
TARGET_CHANNELS = 1


@dataclass(frozen=True)
class Span:
    """One response, located in the original recording by its own waveform."""

    name: str
    source: str
    start_seconds: float
    duration_seconds: float
    confidence: float


def read_wave(path: Path) -> np.ndarray:
    with wave.open(str(path)) as handle:
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float64)


def correlation(left: np.ndarray, right: np.ndarray) -> float:
    """Pearson correlation over the overlapping prefix. 1.0 means identical."""
    length = min(len(left), len(right))
    if length == 0:
        return 0.0
    a = left[:length] - left[:length].mean()
    b = right[:length] - right[:length].mean()
    scale = float(np.sqrt((a * a).sum() * (b * b).sum()))
    return float((a * b).sum() / scale) if scale else 0.0


class SourceLocator:
    """Finds clips inside one recording, by correlation against the whole thing.

    The recording's spectrum is computed once and reused: a 28-minute source is
    27 M samples, and transforming it per clip would dominate the run.
    """

    def __init__(self, reference: np.ndarray, longest_clip: int) -> None:
        self._reference = reference
        self._size = 1 << (len(reference) + longest_clip - 1).bit_length()
        self._spectrum = np.fft.rfft(reference, self._size)

    def locate(self, clip: np.ndarray) -> tuple[float, float]:
        """Return (seconds into the recording, correlation at that point)."""
        product = self._spectrum * np.conj(np.fft.rfft(clip, self._size))
        lag = int(np.argmax(np.fft.irfft(product, self._size)[: len(self._reference)]))
        return (
            lag / CHUNK_RATE,
            correlation(self._reference[lag : lag + len(clip)], clip),
        )


def decode(source_audio: Path, destination: Path, rate: int) -> Path:
    """Decode the whole source once, from the start, at one rate."""
    run_ffmpeg(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source_audio), "-ar", str(rate),
         "-ac", str(TARGET_CHANNELS), "-c:a", "pcm_s16le", str(destination)],
        destination,
        source_audio.name,
    )
    return destination


def read_spans(clips_dir: Path, source: str, locator: SourceLocator) -> list[Span]:
    spans: list[Span] = []
    for clip_path in sorted(clips_dir.glob(f"{source}-*.wav")):
        if CLIP_NAME.match(clip_path.stem) is None:
            raise ValueError(f"unexpected clip name: {clip_path.name}")
        clip = read_wave(clip_path)
        start, confidence = locator.locate(clip)
        spans.append(
            Span(
                name=clip_path.stem,
                source=source,
                start_seconds=start,
                duration_seconds=len(clip) / CHUNK_RATE,
                confidence=confidence,
            )
        )
    return spans


def run_ffmpeg(command: list[str], output: Path, label: str) -> None:
    completed = subprocess.run(command, capture_output=True)
    if completed.returncode != 0 or not output.exists():
        detail = completed.stderr.decode("utf-8", "replace").strip().splitlines()
        raise RuntimeError(f"ffmpeg failed on {label}: {detail[-1] if detail else 'no output'}")


def write_wave(path: Path, samples: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(TARGET_CHANNELS)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.astype(np.int16).tobytes())
    path.chmod(0o600)


def cut(full: np.ndarray, span: Span, output_dir: Path) -> Path:
    """Slice by sample index out of the fully decoded source.

    Not ``ffmpeg -ss`` per clip: seeking and decoding-from-the-start do not share
    a time origin on this file, and every clip came out shifted. Slicing an array
    that was decoded once cannot drift.
    """
    start = round(span.start_seconds * TARGET_RATE)
    length = round(span.duration_seconds * TARGET_RATE)
    output = output_dir / f"{span.name}.wav"
    write_wave(output, full[start : start + length], TARGET_RATE)
    return output


def verify(output: Path, original: Path) -> float:
    """Prove the 48 kHz cut still reproduces the clip it was located from."""
    with tempfile.TemporaryDirectory(prefix="winter-verify-") as staging:
        downsampled = Path(staging) / "check.wav"
        run_ffmpeg(
            ["ffmpeg", "-v", "error", "-y", "-i", str(output), "-ar", str(CHUNK_RATE),
             "-ac", "1", "-c:a", "pcm_s16le", str(downsampled)],
            downsampled,
            output.name,
        )
        return correlation(read_wave(original), read_wave(downsampled))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reference 응답 클립을 원본에서 다시 자릅니다.")
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--source", default="source-003")
    parser.add_argument(
        "--review-dir",
        type=Path,
        help="가장 긴 클립 몇 개를 여기에 복사합니다. 사람이 듣기 위한 경로입니다.",
    )
    parser.add_argument("--review-count", type=int, default=3)
    parser.add_argument(
        "--minimum-correlation",
        type=float,
        default=0.99,
        help="이 값 아래면 잘못 잘린 것으로 보고 실패합니다.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    root: Path = arguments.storage_root
    clips_dir = root / "derived" / "audio" / "responses"
    output_dir = root / "derived" / "audio" / "responses-48k"
    source_audio = root / "raw" / "audio" / "candidate-001" / f"{arguments.source}.webm"

    if not source_audio.exists():
        print(f"원본 오디오가 없습니다: {source_audio}", file=sys.stderr)
        return 1

    clips = sorted(clips_dir.glob(f"{arguments.source}-*.wav"))
    if not clips:
        print(f"{arguments.source}에 해당하는 클립이 없습니다.", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    scores: list[tuple[float, Span]] = []

    with tempfile.TemporaryDirectory(prefix="winter-locate-") as staging:
        # Both decodes start at sample zero of the same file, so a position found
        # in one maps to the other by rate alone.
        reference = read_wave(decode(source_audio, Path(staging) / "at16k.wav", CHUNK_RATE))
        longest_samples = max(len(read_wave(clip)) for clip in clips)
        print(f"원본 {len(reference) / CHUNK_RATE / 60:.1f}분에서 클립 {len(clips)}개를 찾는 중...")
        spans = read_spans(clips_dir, arguments.source, SourceLocator(reference, longest_samples))

        unfound = [span for span in spans if span.confidence < arguments.minimum_correlation]
        if unfound:
            print(f"\n원본에서 찾지 못한 클립 {len(unfound)}개:", file=sys.stderr)
            for span in unfound[:10]:
                print(f"  {span.name} (일치도 {span.confidence:.4f})", file=sys.stderr)
            return 1

        full = read_wave(decode(source_audio, Path(staging) / "at48k.wav", TARGET_RATE))
        for span in spans:
            written = cut(full, span, output_dir)
            scores.append((verify(written, clips_dir / f"{span.name}.wav"), span))

    total = sum(span.duration_seconds for span in spans)
    worst_score, worst_span = min(scores, key=lambda pair: pair[0])
    print(f"클립 {len(spans)}개 | 총 {total:.1f}초 ({total / 60:.1f}분) | {TARGET_RATE} Hz mono")
    print(f"저장: {output_dir}")
    print(f"최저 일치도: {worst_score:.4f} ({worst_span.name})")

    failed = [span for score, span in scores if score < arguments.minimum_correlation]
    if failed:
        print(f"\n{len(failed)}개가 원본 클립과 일치하지 않습니다:", file=sys.stderr)
        for span in failed[:10]:
            print(f"  {span.name}", file=sys.stderr)
        return 1

    if arguments.review_dir is not None:
        arguments.review_dir.mkdir(parents=True, exist_ok=True)
        longest = sorted(spans, key=lambda span: span.duration_seconds, reverse=True)
        print("\n들어볼 클립:")
        for rank, span in enumerate(longest[: arguments.review_count], start=1):
            copied = arguments.review_dir / f"{rank:02d}-{span.name}.wav"
            shutil.copyfile(output_dir / f"{span.name}.wav", copied)
            copied.chmod(0o600)
            print(f"  {copied}  ({span.duration_seconds:.1f}초)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
