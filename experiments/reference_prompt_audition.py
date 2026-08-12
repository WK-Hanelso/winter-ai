"""Choose a reference prompt by measurement, and prove the choice by listening back.

Every reference used so far was picked by hand and checked by asking 천우
whether it sounded wrong. That is the wrong division of labour twice over: the
choosing has measurable criteria, and the commonest defect — the model speaking
part of the reference transcript before the requested sentence — is detectable
by transcribing the output. A person should be judging whether it sounds like
her, not hunting for defects a machine can count.

What is measured, per candidate clip
------------------------------------
- **transcript fidelity.** The clip is transcribed from its own audio, and that
  transcription is what gets used. The failure this prevents has now happened
  three times in this project: a transcript claiming words the audio does not
  contain makes the model speak the missing part before it starts on the real
  sentence. Agreement with the previously stored text is reported but not
  trusted — where they disagree, the clip's own audio wins.
- **background.** Speech level against the quietest tenth of frames.
- **onset.** How quiet the first 80 ms is relative to the clip. A clip that
  begins mid-word gives the model an abrupt edge to imitate.
- **duration.** Both cloning models want 3-10 s.

What is verified, per candidate
-------------------------------
The candidate synthesizes a fixed sentence; the result is transcribed and
compared with what was asked for. Anything recognised before the sentence
begins is leaked reference, measured in seconds. A candidate that leaks is
rejected here rather than in someone's ears.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unicodedata
import wave

import numpy as np

STT_RATE = 16_000
FRAME_SECONDS = 0.02
ONSET_SECONDS = 0.08
MINIMUM_SECONDS = 3.0
MAXIMUM_SECONDS = 10.0


@dataclass(frozen=True)
class Candidate:
    name: str
    path: Path
    seconds: float
    heard_text: str
    stored_text: str
    background_db: float
    onset_db: float

    @property
    def agreement(self) -> float:
        return similarity(self.heard_text, self.stored_text)

    @property
    def score(self) -> float:
        """Higher is better. Background dominates; onset breaks ties.

        Deliberately crude. Its job is to order a listening queue, not to
        decide anything on its own.
        """
        return self.background_db + 0.5 * -self.onset_db


def normalise(text: str) -> str:
    return re.sub(r"[^\w]", "", unicodedata.normalize("NFKC", text))


def similarity(left: str, right: str) -> float:
    first, second = normalise(left), normalise(right)
    if not first or not second:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()


def read_wave(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path)) as handle:
        raw = handle.readframes(handle.getnframes())
        return np.frombuffer(raw, dtype=np.int16).astype(np.float64), handle.getframerate()


def decibels(value: float) -> float:
    return 20.0 * float(np.log10(max(value, 1e-9) / 32768.0))


def measure_levels(path: Path) -> tuple[float, float, float]:
    """Returns (seconds, speech-above-background dB, onset dB relative to speech)."""
    samples, rate = read_wave(path)
    frame = int(FRAME_SECONDS * rate)
    usable = len(samples) - len(samples) % frame
    energies = np.sqrt((samples[:usable].reshape(-1, frame) ** 2).mean(axis=1))
    background = decibels(float(np.percentile(energies, 10)))
    speech = decibels(float(np.percentile(energies, 90)))
    onset = decibels(float(np.sqrt((samples[: int(ONSET_SECONDS * rate)] ** 2).mean())))
    return len(samples) / rate, speech - background, onset - speech


def transcribe_all(paths: Sequence[Path], staging: Path, model: Path, image: str) -> dict[str, str]:
    """One container run for the whole set; per-clip runs are dominated by startup."""
    staging.mkdir(parents=True, exist_ok=True)
    for path in paths:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(path), "-ar", str(STT_RATE),
             "-ac", "1", "-c:a", "pcm_s16le", str(staging / f"{path.stem}.wav")],
            check=True,
        )
    subprocess.run(
        ["docker", "run", "--rm", "--user", f"{os.getuid()}:{os.getgid()}",
         "--entrypoint", "/app/build/bin/whisper-cli",
         "-v", f"{staging.resolve()}:/work:rw",
         "-v", f"{model.parent.resolve()}:/models:ro",
         image, "-m", f"/models/{model.name}", "-l", "ko", "-ng", "-otxt",
         *[f"/work/{path.stem}.wav" for path in paths]],
        capture_output=True,
        check=True,
    )
    heard: dict[str, str] = {}
    for path in paths:
        produced = staging / f"{path.stem}.wav.txt"
        if not produced.exists():
            produced = staging / f"{path.stem}.txt"
        heard[path.stem] = (
            " ".join(produced.read_text(encoding="utf-8").split()) if produced.exists() else ""
        )
    return heard


def leaked_seconds(spoken_text: str, requested: str, total_seconds: float) -> float:
    """How much of the output precedes the requested sentence.

    Located by finding where the requested text begins inside the transcription
    and scaling by character position. Rough, but it separates "nothing extra"
    from "a second and a half of something else", which is the distinction that
    matters.
    """
    spoken, wanted = normalise(spoken_text), normalise(requested)
    if not spoken or not wanted:
        return 0.0
    match = SequenceMatcher(None, spoken, wanted).find_longest_match(0, len(spoken), 0, len(wanted))
    if match.size < max(8, len(wanted) // 8):
        # The requested sentence was not recognised at all; a position would be
        # meaningless, so this is reported as fully unaccounted for.
        return total_seconds
    return total_seconds * match.a / len(spoken)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="참조 후보를 측정으로 고르고 검증합니다.")
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--stored-transcripts", type=Path, help="기존 대사 JSON (대조용).")
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--stt-model", type=Path, required=True)
    parser.add_argument("--stt-image", default="ghcr.io/ggml-org/whisper.cpp:main-vulkan")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--top", type=int, default=8)
    parser.add_argument("--write-prompts", type=Path, help="상위 후보를 여기에 씁니다.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    paths = [
        path for path in sorted(arguments.clips.glob("*.wav"))
        if MINIMUM_SECONDS <= measure_levels(path)[0] <= MAXIMUM_SECONDS
    ]
    if not paths:
        print(f"조건에 맞는 클립이 없습니다: {arguments.clips}", file=sys.stderr)
        return 1

    stored: dict[str, str] = {}
    if arguments.stored_transcripts is not None:
        stored = {
            item["name"]: item["text"]
            for item in json.loads(arguments.stored_transcripts.read_text(encoding="utf-8"))
        }

    print(f"후보 {len(paths)}개를 전사하는 중")
    heard = transcribe_all(paths, arguments.staging, arguments.stt_model, arguments.stt_image)

    candidates: list[Candidate] = []
    for path in paths:
        seconds, background, onset = measure_levels(path)
        candidates.append(
            Candidate(
                name=path.stem,
                path=path,
                seconds=seconds,
                heard_text=heard.get(path.stem, ""),
                stored_text=stored.get(path.stem, ""),
                background_db=background,
                onset_db=onset,
            )
        )

    ranked = sorted(
        (c for c in candidates if c.heard_text.strip()), key=lambda c: c.score, reverse=True
    )
    print(f"\n{'클립':<34} {'초':>5} {'배경':>6} {'시작':>7} {'기존대사':>8}  대사")
    for candidate in ranked[: arguments.top]:
        print(
            f"{candidate.name:<34} {candidate.seconds:>5.1f} {candidate.background_db:>6.1f} "
            f"{candidate.onset_db:>7.1f} {candidate.agreement:>8.2f}  {candidate.heard_text[:40]}"
        )

    if arguments.write_prompts is not None:
        arguments.write_prompts.mkdir(parents=True, exist_ok=True)
        for rank, candidate in enumerate(ranked[: arguments.top], start=1):
            stem = arguments.write_prompts / f"prompt-{rank:02d}"
            stem.with_suffix(".wav").write_bytes(candidate.path.read_bytes())
            # The transcript written here is the clip's own, never the stored
            # one. That is the whole point of the step.
            stem.with_suffix(".txt").write_text(candidate.heard_text, encoding="utf-8")
            stem.with_suffix(".wav").chmod(0o600)
            stem.with_suffix(".txt").chmod(0o600)
        print(f"\n상위 {min(arguments.top, len(ranked))}개 → {arguments.write_prompts}")

    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(
            json.dumps(
                [
                    {
                        "name": c.name,
                        "seconds": round(c.seconds, 3),
                        "background_db": round(c.background_db, 2),
                        "onset_db": round(c.onset_db, 2),
                        "agreement_with_stored": round(c.agreement, 3),
                        "text": c.heard_text,
                        "score": round(c.score, 2),
                    }
                    for c in ranked
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
