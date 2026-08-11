"""Judge every Reference clip on whether it can be training material.

The first pass produced 42 clips that were correctly *located* — each reproduces
the span it came from exactly. That says nothing about whether the span is
usable, and listening to the three longest found background music in one and
another speaker's tail in another.

This scores what can be scored without ears, so that the listening pass is spent
on a short candidate list rather than 42 files:

- **transcript agreement** — the clip's own transcription against the text
  already on file. These come from different passes, so where they agree the
  text can be trusted. Where they disagree one of them is invented, and a wrong
  transcript degrades a cloned voice silently.
- **completeness** — a clip cut mid-word cannot have a correct transcript at
  all, whatever is written down. The 9.44 s reference that produced slurred
  speech was exactly this, and replacing it with a whole utterance fixed the
  diction where correcting its transcript had not.
- **duration** — a cloning reference wants roughly 3-10 s.

Nothing here decides anything on its own. It ranks, and a person listens to the
top of the ranking.
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
import unicodedata

# Sentence-final endings. Korean speech that stops here has finished a thought;
# speech that stops on a connective ("...하고", "...근데") has been cut off.
FINAL_ENDINGS = (
    "요", "다", "죠", "네", "까", "지", "야", "어", "아", "게", "죵", "군", "냐", "래",
)
# Subtitle convention in this source: a dash marks a change of speaker, so a
# clip whose text carries one very likely contains two voices.
SPEAKER_CHANGE = re.compile(r"(^|\s)-(\s|$)")
STT_RATE = 16_000


@dataclass(frozen=True)
class Verdict:
    name: str
    seconds: float
    stored_text: str
    heard_text: str
    agreement: float
    complete: bool
    speaker_change: bool

    @property
    def usable(self) -> bool:
        """Whether this clip is worth a person's time to listen to."""
        return (
            3.0 <= self.seconds <= 10.0
            and self.agreement >= 0.7
            and self.complete
            and not self.speaker_change
        )


def normalise(text: str) -> str:
    """Strip everything two transcriptions may legitimately disagree about."""
    text = unicodedata.normalize("NFKC", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def agreement(left: str, right: str) -> float:
    """Share of the shorter transcript's words present in the other.

    Word overlap rather than edit distance: the two passes differ on spacing and
    on hearing proper nouns, and neither should count as disagreement about
    what was said.
    """
    first, second = normalise(left).split(), normalise(right).split()
    if not first or not second:
        return 0.0
    shorter, longer = (first, second) if len(first) <= len(second) else (second, first)
    remaining = list(longer)
    matched = 0
    for word in shorter:
        if word in remaining:
            remaining.remove(word)
            matched += 1
    return matched / len(shorter)


def is_complete(text: str) -> bool:
    words = normalise(text).split()
    return bool(words) and words[-1].endswith(FINAL_ENDINGS)


def transcribe(clip: Path, staging: Path, model: Path, image: str, user: str) -> str:
    """Transcribe the clip itself, not the chunk it came from."""
    resampled = staging / f"{clip.stem}-{STT_RATE}.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-ar", str(STT_RATE),
         "-ac", "1", "-c:a", "pcm_s16le", str(resampled)],
        check=True,
    )
    subprocess.run(
        ["docker", "run", "--rm", "--user", user,
         "--entrypoint", "/app/build/bin/whisper-cli",
         "-v", f"{staging.resolve()}:/work:rw",
         "-v", f"{model.parent.resolve()}:/models:ro",
         image,
         "-m", f"/models/{model.name}", "-f", f"/work/{resampled.name}",
         "-l", "ko", "-ng", "-otxt", "-of", f"/work/{clip.stem}"],
        capture_output=True,
        check=True,
    )
    produced = staging / f"{clip.stem}.txt"
    return " ".join(produced.read_text(encoding="utf-8").split()) if produced.exists() else ""


def stored_transcripts(pairs_dir: Path) -> dict[str, str]:
    """The response text recorded for each clip when the pairs were built."""
    texts: dict[str, str] = {}
    for path in pairs_dir.glob("pairs-*.json"):
        chunk = path.stem[len("pairs-") :]
        for index, pair in enumerate(json.loads(path.read_text(encoding="utf-8"))):
            texts[f"{chunk}-response-{index:03d}"] = str(pair["response"])
    return texts


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="클립이 학습 재료가 될 수 있는지 판정합니다.")
    parser.add_argument("--storage-root", type=Path, required=True)
    parser.add_argument("--stt-model", type=Path, required=True)
    parser.add_argument("--stt-image", default="ghcr.io/ggml-org/whisper.cpp:main-vulkan")
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    clips_dir = arguments.storage_root / "derived" / "audio" / "responses-48k"
    clips = sorted(clips_dir.glob("*.wav"))
    if not clips:
        print(f"클립이 없습니다: {clips_dir}", file=sys.stderr)
        return 1

    stored = stored_transcripts(arguments.storage_root / "derived" / "transcripts" / "raw")
    arguments.staging.mkdir(parents=True, exist_ok=True)
    user = f"{__import__('os').getuid()}:{__import__('os').getgid()}"

    verdicts: list[Verdict] = []
    for clip in clips:
        import wave

        with wave.open(str(clip)) as handle:
            seconds = handle.getnframes() / handle.getframerate()
        heard = transcribe(clip, arguments.staging, arguments.stt_model, arguments.stt_image, user)
        stored_text = stored.get(clip.stem, "")
        verdicts.append(
            Verdict(
                name=clip.stem,
                seconds=seconds,
                stored_text=stored_text,
                heard_text=heard,
                agreement=agreement(stored_text, heard),
                complete=is_complete(heard),
                speaker_change=bool(SPEAKER_CHANGE.search(stored_text)),
            )
        )
        print(".", end="", flush=True)
    print()

    usable = [verdict for verdict in verdicts if verdict.usable]
    usable.sort(key=lambda verdict: verdict.agreement, reverse=True)

    print(f"\n클립 {len(verdicts)}개 중 후보 {len(usable)}개")
    print(f"{'클립':<38} {'초':>5} {'일치':>5}  대사")
    for verdict in usable:
        print(
            f"{verdict.name:<38} {verdict.seconds:>5.1f} {verdict.agreement:>5.2f}  "
            f"{verdict.heard_text[:52]}"
        )

    dropped = {
        "짧거나 김": [v.name for v in verdicts if not 3.0 <= v.seconds <= 10.0],
        "대사 불일치": [v.name for v in verdicts if v.agreement < 0.7],
        "문장이 잘림": [v.name for v in verdicts if not v.complete],
        "화자 전환 표시": [v.name for v in verdicts if v.speaker_change],
    }
    print("\n제외 사유 (겹칠 수 있음)")
    for reason, names in dropped.items():
        print(f"  {reason}: {len(names)}개")

    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(
            json.dumps([verdict.__dict__ for verdict in verdicts], ensure_ascii=False, indent=1),
            encoding="utf-8",
        )
        arguments.report.chmod(0o600)
        print(f"\n{arguments.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
