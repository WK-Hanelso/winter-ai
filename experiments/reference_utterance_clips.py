"""Cut Reference speech into whole utterances, for voice cloning material.

The first cut took the pieces diarization produced and used them as they were.
That was right for building (prompt, response) pairs — diarization answers "who
is speaking" and a pair only needs to know that. It is the wrong unit for
teaching a voice.

Of the 42 clips it made, 7 survived an audit: 17 stopped mid-sentence, 12 carry
a speaker change in their subtitles, and 24 fall outside the 3-10 s a cloning
reference wants. Roughly forty usable seconds out of three and a half minutes.
A clip cut mid-word cannot have a correct transcript no matter what is written
down, and a wrong transcript degrades a cloned voice with no error anywhere.

So this cuts on a different boundary. Inside stretches where only the Reference
speaks, it splits where she finishes a sentence — a word ending in a
sentence-final form, followed by a pause. Word timings and the speaker timeline
both already exist; nothing is re-derived.

Each clip is then transcribed from its own audio rather than inherited from the
chunk, because the boundary is exactly where the chunk-level text was wrong.
"""

from __future__ import annotations

import argparse
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
import wave

import numpy as np

# Korean sentence-final endings. Speech stopping here has finished a thought;
# stopping on a connective ("...하고", "...근데") has been interrupted.
FINAL_ENDINGS = ("요", "다", "죠", "네", "까", "야", "어", "지", "군", "래", "봐", "죵")
CUE = re.compile(r"(\d+):(\d+):(\d+\.\d+) --> (\d+):(\d+):(\d+\.\d+)")
STT_RATE = 16_000
TARGET_RATE = 48_000
# A pause this long after a sentence ending is a boundary rather than a breath.
# Shorter than this and the speaker is still going.
SENTENCE_PAUSE = 0.35
# The window a cloning reference wants. Both candidate models agree on it.
MINIMUM_SECONDS = 3.0
MAXIMUM_SECONDS = 10.0
# Diarization boundaries are only accurate to a couple of frames, and a late
# end is how another speaker's first syllable gets in. Give both ends room.
SPEAKER_MARGIN = 0.20


@dataclass(frozen=True)
class Word:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class Utterance:
    source: str
    chunk_offset: float
    start: float
    end: float
    words: tuple[str, ...]

    @property
    def seconds(self) -> float:
        return self.end - self.start

    @property
    def absolute_start(self) -> float:
        return self.chunk_offset + self.start

    @property
    def text(self) -> str:
        return " ".join(self.words)

    @property
    def name(self) -> str:
        return f"{self.source}-{int(self.chunk_offset)}-utt-{int(self.start * 100):06d}"


def read_words(path: Path) -> list[Word]:
    words: list[Word] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for index, line in enumerate(lines):
        matched = CUE.match(line.strip())
        if matched is None:
            continue
        groups = matched.groups()
        start = int(groups[0]) * 3600 + int(groups[1]) * 60 + float(groups[2])
        end = int(groups[3]) * 3600 + int(groups[4]) * 60 + float(groups[5])
        text = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if text and end > start:
            words.append(Word(start, end, text))
    return words


def merge(spans: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Union of overlapping spans, so each stretch is counted once."""
    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def reference_spans(path: Path, speaker: int) -> list[tuple[float, float]]:
    """Stretches where the Reference speaks and nobody else does.

    Merged before subtracting. The saved timeline lists many short overlapping
    spans, and removing each other-speaker span from each Reference span one at
    a time shredded the timeline into pieces too small to hold a sentence.

    Both sides are unions first, then one subtraction, then one margin. The
    margin is because diarization boundaries are only good to a couple of
    frames, and a late boundary is how the next speaker's first syllable gets in.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))["spans"]
    mine = merge([(start, end) for start, end, who in raw if who == speaker])
    others = merge([(start, end) for start, end, who in raw if who != speaker])

    kept: list[tuple[float, float]] = []
    for start, end in mine:
        pieces = [(start, end)]
        for other_start, other_end in others:
            remaining: list[tuple[float, float]] = []
            for piece_start, piece_end in pieces:
                if other_end <= piece_start or other_start >= piece_end:
                    remaining.append((piece_start, piece_end))
                    continue
                if piece_start < other_start:
                    remaining.append((piece_start, other_start))
                if other_end < piece_end:
                    remaining.append((other_end, piece_end))
            pieces = remaining
        for piece_start, piece_end in pieces:
            shrunk = (piece_start + SPEAKER_MARGIN, piece_end - SPEAKER_MARGIN)
            if shrunk[1] - shrunk[0] >= MINIMUM_SECONDS:
                kept.append(shrunk)
    return sorted(kept)


def ends_sentence(text: str) -> bool:
    stripped = re.sub(r"[^\w]", "", text)
    return bool(stripped) and stripped.endswith(FINAL_ENDINGS)


def split_into_utterances(words: Sequence[Word]) -> Iterator[list[Word]]:
    """Break the whole transcript wherever a finished sentence meets a pause.

    Run over every word first, not over the words inside a speaker span. Cutting
    to the span first makes the *start* of each group land wherever the span
    began, which is usually mid-sentence: the first attempt produced "들릴 수
    있지만 노래보다는..." because only the ending was ever checked.
    """
    current: list[Word] = []
    for index, word in enumerate(words):
        current.append(word)
        following = words[index + 1] if index + 1 < len(words) else None
        pause = (following.start - word.end) if following is not None else float("inf")
        if ends_sentence(word.text) and pause >= SENTENCE_PAUSE:
            yield current
            current = []
    if current:
        yield current


def utterances(words: Sequence[Word], spans: Sequence[tuple[float, float]],
               source: str, offset: float) -> list[Utterance]:
    """Sentences that sit entirely inside one Reference-only stretch.

    Both boundaries are therefore sentence boundaries, and the whole sentence
    belongs to one speaker. A sentence that straddles a speaker change is
    dropped rather than trimmed — trimming is what produced fragments.
    """
    found: list[Utterance] = []
    for group in split_into_utterances(words):
        if not group:
            continue
        text = " ".join(word.text for word in group)
        # Whisper emits bracketed notes for non-speech ("[이 영상은 ...]").
        # They are not the Reference talking and their timings mean nothing.
        if "[" in text or "]" in text:
            continue
        start, end = group[0].start, group[-1].end
        if not any(span_start <= start and end <= span_end for span_start, span_end in spans):
            continue
        candidate = Utterance(
            source=source,
            chunk_offset=offset,
            start=start,
            end=end,
            words=tuple(word.text for word in group),
        )
        if MINIMUM_SECONDS <= candidate.seconds <= MAXIMUM_SECONDS:
            found.append(candidate)
    return found


def read_wave(path: Path) -> np.ndarray:
    with wave.open(str(path)) as handle:
        return np.frombuffer(handle.readframes(handle.getnframes()), dtype=np.int16).astype(
            np.float64
        )


def write_wave(path: Path, samples: np.ndarray, rate: int) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.astype(np.int16).tobytes())
    path.chmod(0o600)


def decode(source_audio: Path, destination: Path, rate: int) -> Path:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(source_audio), "-ar", str(rate),
         "-ac", "1", "-c:a", "pcm_s16le", str(destination)],
        check=True,
    )
    return destination


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Reference 발화를 문장 단위로 잘라냅니다.")
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--source", default="source-003")
    parser.add_argument(
        "--speaker",
        type=int,
        default=1,
        help="화자 번호. source-003에서는 1이 Reference로 확인되었습니다.",
    )
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    root: Path = arguments.storage_root
    pilot = root / "derived" / "audio" / "stt-pilot"
    reports = root / "reports"
    source_audio = root / "raw" / "audio" / "candidate-001" / f"{arguments.source}.webm"
    output_dir = root / "derived" / "audio" / "utterances"

    if not source_audio.exists():
        print(f"원본 오디오가 없습니다: {source_audio}", file=sys.stderr)
        return 1

    found: list[Utterance] = []
    for spans_path in sorted(reports.glob(f"spans-{arguments.source}-*.json")):
        chunk = spans_path.stem[len("spans-") :]
        offset = float(chunk.rsplit("-", 2)[-2])
        vtt = pilot / f"{chunk}-words.vtt"
        if not vtt.exists():
            print(f"  건너뜀 (word VTT 없음): {chunk}", file=sys.stderr)
            continue
        found.extend(
            utterances(
                read_words(vtt),
                reference_spans(spans_path, arguments.speaker),
                arguments.source,
                offset,
            )
        )

    if not found:
        print("조건에 맞는 발화가 없습니다.", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    output_dir.chmod(0o700)
    import tempfile

    with tempfile.TemporaryDirectory(prefix="winter-utt-") as staging:
        full = read_wave(decode(source_audio, Path(staging) / "at48k.wav", TARGET_RATE))
        for utterance in found:
            start = round(utterance.absolute_start * TARGET_RATE)
            length = round(utterance.seconds * TARGET_RATE)
            write_wave(output_dir / f"{utterance.name}.wav", full[start : start + length],
                       TARGET_RATE)

    total = sum(utterance.seconds for utterance in found)
    print(f"발화 {len(found)}개 | 총 {total:.1f}초 ({total / 60:.1f}분) | {TARGET_RATE} Hz mono")
    print(f"저장: {output_dir}")
    print(f"\n{'발화':<34} {'초':>5}  대사")
    for utterance in sorted(found, key=lambda item: item.seconds, reverse=True)[:15]:
        print(f"{utterance.name:<34} {utterance.seconds:>5.1f}  {utterance.text[:54]}")

    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(
            json.dumps(
                [
                    {
                        "name": utterance.name,
                        "seconds": round(utterance.seconds, 3),
                        "absolute_start": round(utterance.absolute_start, 3),
                        "text": utterance.text,
                    }
                    for utterance in found
                ],
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        arguments.report.chmod(0o600)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
