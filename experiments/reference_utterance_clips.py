"""Cut Reference speech into whole sentences, for voice cloning material.

The first cut took the pieces diarization produced and used them as they were.
That was right for building (prompt, response) pairs — diarization answers "who
is speaking" and a pair only needs to know that. It is the wrong unit for
teaching a voice.

Of the 42 clips it made, 7 survived an audit: 17 stopped mid-sentence, 12 carry
a speaker change in their subtitles, and 24 fall outside the 3-10 s a cloning
reference wants. Roughly forty usable seconds out of three and a half minutes.

Sentences come from the transcriber, not from here
--------------------------------------------------
Two attempts to find sentence boundaries in the word-level transcript failed,
and both failures were the same shape: the data had no boundaries in it. Those
cues carry one word each, sit flush against one another, and have no
punctuation, so a rule looking for "a sentence ending followed by a pause"
found one 296-second sentence. Splitting inside speaker spans instead made
every clip start wherever the span did, which is mid-sentence.

So the chunks were transcribed again normally. Whisper's ordinary output is
already sentences, with punctuation and its own start and end times, and it
finds 49 per chunk where the rule found between 1 and 15.

That also fixes the transcript problem for free. The clip boundary and the text
boundary now come from the same decision, so a clip cannot disagree with its own
transcript — which is exactly what degraded the first cloning attempt.

Speaker attribution is by share, not by exclusion
-------------------------------------------------
A sentence is kept when the Reference holds most of it and nobody else holds
much. Requiring no overlap at all removed everything: this is an interview and
the host's agreement noises land on top of her constantly, so subtracting every
overlapping span left two chunks with no Reference stretch whatsoever.

Solo recordings skip all of that
--------------------------------
``--solo`` reads one transcript for a whole recording and keeps every sentence,
because there is nobody else in it. A broadcast where the Reference talks alone
needs no diarization, no per-chunk identification of which numbered speaker she
is, and no removal of an interviewer.

Which matters, because the numbered speakers are not comparable across chunks.
Diarization labels are stable inside a processing window and not between them:
speaker 0 is the host in one chunk of the interview and the Reference in
another. Two of six chunks had ever been checked against the enrolment
centroid, and applying either answer to all six is simply wrong.

The interview was the material at hand because it was the material that made
conversational pairs. Voice cloning does not want a conversation.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import json
from pathlib import Path
import re
import subprocess
import sys
import wave

import numpy as np

CUE = re.compile(r"(\d+):(\d+):(\d+\.\d+) --> (\d+):(\d+):(\d+\.\d+)")
TARGET_RATE = 48_000
# The window a cloning reference wants. Both candidate models agree on it, and
# it is the default because a clip cut here is usable as either.
#
# It is not what training wants. seed-vc's fine-tuning loader accepts 1 to 30
# seconds (data/ft_dataset.py) and skips anything outside that, so cutting a
# training set at 3-10 s throws away every long sentence and every short one for
# a constraint that belongs to inference. --min-seconds and --max-seconds move
# the window without pretending one number serves both jobs.
MINIMUM_SECONDS = 3.0
MAXIMUM_SECONDS = 10.0
# The Reference has to hold most of the sentence, and the others almost none of
# it. Two thresholds rather than "no overlap at all": this is an interview, the
# host agrees over her constantly, and demanding a clean stretch left two chunks
# with nothing. A short "네" on top of her speech does not spoil the clip; the
# host saying a whole clause does.
MINIMUM_REFERENCE_SHARE = 0.80
MAXIMUM_OTHER_SHARE = 0.15
# Silence long enough to mean she stopped, not that she took a breath. Used only
# when joining a solo transcript's cues back into utterances.
JOIN_GAP = 0.6


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


def read_cues(path: Path) -> list[Word]:
    """Read a WebVTT. Each cue is one sentence, as the transcriber divided it."""
    cues: list[Word] = []
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
            cues.append(Word(start, end, text))
    return cues


def merge(spans: Sequence[tuple[float, float]]) -> list[tuple[float, float]]:
    """Union of overlapping spans, so each stretch is counted once."""
    merged: list[tuple[float, float]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def covered(spans: Sequence[tuple[float, float]], start: float, end: float) -> float:
    """How much of [start, end] the given spans occupy, in seconds."""
    return sum(
        max(0.0, min(end, span_end) - max(start, span_start)) for span_start, span_end in spans
    )


Spans = list[tuple[float, float]]


def speaker_spans(path: Path, speaker: int) -> tuple[Spans, Spans]:
    """The Reference's stretches and everyone else's, each merged."""
    raw = json.loads(path.read_text(encoding="utf-8"))["spans"]
    return (
        merge([(start, end) for start, end, who in raw if who == speaker]),
        merge([(start, end) for start, end, who in raw if who != speaker]),
    )


def chunk_speaker(reports: Path, chunk: str, fallback: int | None) -> int | None:
    """Which numbered speaker is the Reference in this chunk.

    Diarization numbers are stable inside a chunk and meaningless between them:
    in source-003 the Reference is speaker 2, then 1, then 1, then 0, then 1.
    One ``--speaker`` applied to every chunk therefore cuts the wrong person's
    speech in most of them, which is how a 28-minute interview yielded three
    minutes.

    So the per-chunk identification report decides, and a chunk it could not
    judge is skipped rather than guessed at. ``--speaker`` remains as a fallback
    for chunks with no report, because that is the older behaviour and some
    material was cut before this existed.
    """
    report = reports / f"who-is-reference-{chunk}.json"
    if not report.exists():
        return fallback
    identified = json.loads(report.read_text(encoding="utf-8"))["identified_speaker"]
    if not identified.get("is_confident"):
        return None
    return identified["reference_speaker"]


def join_cues(cues: Sequence[Word], maximum_seconds: float = MAXIMUM_SECONDS) -> list[Word]:
    """Join neighbouring cues into utterances of a usable length.

    The solo transcript is cut far finer than a cloning reference wants — 394 of
    its 453 cues are under three seconds, and only 43 land in the 3-10 s window
    on their own. In a recording with one speaker, consecutive cues are the same
    person continuing, so joining them is safe in a way it would not be in the
    interview.

    A join stops at ``JOIN_GAP`` of silence, because that is where she stopped
    rather than paused, and never crosses ``MAXIMUM_SECONDS``.
    """
    joined: list[Word] = []
    current: list[Word] = []
    for cue in cues:
        if current:
            gap = cue.start - current[-1].end
            span = cue.end - current[0].start
            if gap > JOIN_GAP or span > maximum_seconds:
                joined.append(
                    Word(current[0].start, current[-1].end, " ".join(c.text for c in current))
                )
                current = []
        current.append(cue)
    if current:
        joined.append(Word(current[0].start, current[-1].end, " ".join(c.text for c in current)))
    return joined


def utterances(
    cues: Sequence[Word],
    mine: Sequence[tuple[float, float]],
    others: Sequence[tuple[float, float]],
    source: str,
    offset: float,
    minimum_seconds: float = MINIMUM_SECONDS,
    maximum_seconds: float = MAXIMUM_SECONDS,
) -> list[Utterance]:
    """Sentences the Reference says, and that nobody else says much of."""
    found: list[Utterance] = []
    for cue in cues:
        seconds = cue.end - cue.start
        if not minimum_seconds <= seconds <= maximum_seconds:
            continue
        # Whisper writes bracketed notes for non-speech ("[음악]", "[이 영상은
        # ...]"). Those are not the Reference talking.
        if "[" in cue.text or "]" in cue.text:
            continue
        if covered(mine, cue.start, cue.end) / seconds < MINIMUM_REFERENCE_SHARE:
            continue
        if covered(others, cue.start, cue.end) / seconds > MAXIMUM_OTHER_SHARE:
            continue
        found.append(
            Utterance(
                source=source,
                chunk_offset=offset,
                start=cue.start,
                end=cue.end,
                words=tuple(cue.text.split()),
            )
        )
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
        help="화자 번호. chunk마다 달라질 수 있으므로 확인된 값만 쓰세요.",
    )
    parser.add_argument(
        "--solo",
        action="store_true",
        help="혼자 말하는 녹음. 화자 판정을 건너뛰고 전체를 하나로 다룹니다.",
    )
    parser.add_argument(
        "--transcript",
        type=Path,
        help="문장 VTT 경로. 생략하면 source 이름에서 찾습니다.",
    )
    parser.add_argument(
        "--min-seconds",
        type=float,
        default=MINIMUM_SECONDS,
        help="기본값은 참조 음성 기준. 학습용은 더 넓게 (seed-vc는 1~30초).",
    )
    parser.add_argument("--max-seconds", type=float, default=MAXIMUM_SECONDS)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    root: Path = arguments.storage_root
    pilot = root / "derived" / "audio" / "stt-pilot"
    reports = root / "reports"
    source_audio = root / "raw" / "audio" / "candidate-001" / f"{arguments.source}.webm"
    output_dir = root / "derived" / "audio" / "utterances" / arguments.source

    if not source_audio.exists():
        print(f"원본 오디오가 없습니다: {source_audio}", file=sys.stderr)
        return 1
    # Per-chunk identification reports answer the speaker question on their own,
    # so --speaker is only needed for material cut before those existed.
    identified = any(reports.glob(f"who-is-reference-{arguments.source}-*.json"))
    if not arguments.solo and arguments.speaker is None and not identified:
        print("--speaker, --solo, 또는 화자 판정 리포트가 필요합니다.", file=sys.stderr)
        return 1

    found: list[Utterance] = []
    if arguments.solo:
        # One transcript for the whole recording, and every sentence kept: with
        # nobody else present there is no attribution to make.
        vtt = arguments.transcript or (pilot / f"{arguments.source}-sentences.vtt")
        if not vtt.exists():
            print(f"문장 VTT가 없습니다: {vtt}", file=sys.stderr)
            return 1
        everything = [(0.0, float("inf"))]
        cues = join_cues(read_cues(vtt), arguments.max_seconds)
        print(f"cue {len(read_cues(vtt))}개 → 이어붙여 {len(cues)}개")
        found = utterances(
            cues, everything, [], arguments.source, 0.0,
            arguments.min_seconds, arguments.max_seconds,
        )
    else:
        for spans_path in sorted(reports.glob(f"spans-{arguments.source}-*.json")):
            chunk = spans_path.stem[len("spans-") :]
            offset = float(chunk.rsplit("-", 2)[-2])
            vtt = pilot / f"{chunk}-sentences.vtt"
            if not vtt.exists():
                print(f"  건너뜀 (문장 VTT 없음): {chunk}", file=sys.stderr)
                continue
            speaker = chunk_speaker(reports, chunk, arguments.speaker)
            if speaker is None:
                print(f"  건너뜀 (화자 판정 없음): {chunk}", file=sys.stderr)
                continue
            mine, others = speaker_spans(spans_path, speaker)
            found.extend(
                utterances(
                    read_cues(vtt), mine, others, arguments.source, offset,
                    arguments.min_seconds, arguments.max_seconds,
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
