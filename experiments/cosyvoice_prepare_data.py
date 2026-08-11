"""Turn the cleaned Reference clips into a CosyVoice training set.

Emits the four files its pipeline expects — ``wav.scp``, ``text``, ``utt2spk``,
``spk2utt`` — after deciding which clips belong in it at all.

Transcripts are read from the audio again here, not carried over. The text on
file was heard through the rain; the clips have since been denoised, and a
transcriber given cleaner audio makes different mistakes. This matters more for
training than for anything else done so far: a wrong transcript teaches the
model that those syllables sound like this audio, and it does so silently. The
first cloning attempt slurred its speech for exactly that reason, from a single
reference whose text was wrong at both ends.

A clip is included when both transcriptions agree, since they come from
different audio and their agreement is evidence. Where they disagree, one of
them is invented and there is no way to tell which from here, so the clip is
dropped rather than guessed at.
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

STT_RATE = 16_000
# Below this the two transcriptions are describing different speech.
MINIMUM_AGREEMENT = 0.7


@dataclass(frozen=True)
class Clip:
    name: str
    path: Path
    seconds: float
    stored_text: str
    heard_text: str
    agreement: float

    @property
    def usable(self) -> bool:
        return self.agreement >= MINIMUM_AGREEMENT and bool(self.heard_text.strip())


def normalise(text: str) -> str:
    """Reduce to the characters that were said, dropping how they were written.

    Spacing goes too. Korean word boundaries are written inconsistently and both
    transcriptions guess at them: "안돼요 켜 달라고" against "안 돼요 켜달라고" is
    the same sentence heard the same way. Compared word by word it scored 0.60
    and the clip was thrown out, which is how this was noticed.
    """
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"[^\w]", "", text)


def agreement(left: str, right: str) -> float:
    """How much of the two transcripts is the same text, 0 to 1.

    Character-level, because the unit that matters is what was said. A real
    mishearing still separates: "실수할 수" against "실사할 수" shares most
    characters but not enough to pass, while a spacing difference shares all of
    them.
    """
    first, second = normalise(left), normalise(right)
    if not first or not second:
        return 0.0
    return SequenceMatcher(None, first, second).ratio()


def transcribe_all(
    clips: Sequence[Path], staging: Path, model: Path, image: str, user: str
) -> dict[str, str]:
    """Transcribe every clip in one container run.

    One run, not one per clip: the model load and container start dominate a
    three-second clip, and doing that 130 times costs more than the
    transcription itself.
    """
    produced_for = {clip.stem: staging / f"{clip.stem}.wav.txt" for clip in clips}
    if all(path.exists() for path in produced_for.values()):
        print("이미 전사된 결과를 재사용합니다.")
        return {
            name: " ".join(path.read_text(encoding="utf-8").split())
            for name, path in produced_for.items()
        }

    for clip in clips:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-ar", str(STT_RATE),
             "-ac", "1", "-c:a", "pcm_s16le", str(staging / f"{clip.stem}.wav")],
            check=True,
        )
    subprocess.run(
        ["docker", "run", "--rm", "--user", user,
         "--entrypoint", "/app/build/bin/whisper-cli",
         "-v", f"{staging.resolve()}:/work:rw",
         "-v", f"{model.parent.resolve()}:/models:ro",
         image,
         "-m", f"/models/{model.name}", "-l", "ko", "-ng", "-otxt",
         *[f"/work/{clip.stem}.wav" for clip in clips]],
        capture_output=True,
        check=True,
    )
    heard: dict[str, str] = {}
    for clip in clips:
        # whisper.cpp writes <input>.txt beside each input when given several.
        produced = staging / f"{clip.stem}.wav.txt"
        if not produced.exists():
            produced = staging / f"{clip.stem}.txt"
        heard[clip.stem] = (
            " ".join(produced.read_text(encoding="utf-8").split()) if produced.exists() else ""
        )
    return heard


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="CosyVoice 학습용 데이터를 준비합니다.")
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--utterances", type=Path, required=True, help="기존 대사 JSON.")
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--stt-model", type=Path, required=True)
    parser.add_argument("--stt-image", default="ghcr.io/ggml-org/whisper.cpp:main-vulkan")
    parser.add_argument("--speaker-id", default="winter")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    paths = sorted(arguments.clips.glob("*.wav"))
    if not paths:
        print(f"클립이 없습니다: {arguments.clips}", file=sys.stderr)
        return 1

    stored = {
        item["name"]: item["text"]
        for item in json.loads(arguments.utterances.read_text(encoding="utf-8"))
    }
    arguments.staging.mkdir(parents=True, exist_ok=True)
    user = f"{os.getuid()}:{os.getgid()}"

    print(f"클립 {len(paths)}개를 다시 전사하는 중 (깨끗해진 음성으로)")
    heard_by_name = transcribe_all(
        paths, arguments.staging, arguments.stt_model, arguments.stt_image, user
    )

    clips: list[Clip] = []
    for path in paths:
        with wave.open(str(path)) as handle:
            seconds = handle.getnframes() / handle.getframerate()
        heard = heard_by_name.get(path.stem, "")
        stored_text = stored.get(path.stem, "")
        clips.append(
            Clip(
                name=path.stem,
                path=path,
                seconds=seconds,
                stored_text=stored_text,
                heard_text=heard,
                agreement=agreement(stored_text, heard),
            )
        )

    usable = [clip for clip in clips if clip.usable]
    total = sum(clip.seconds for clip in usable)
    print(f"클립 {len(clips)}개 중 {len(usable)}개 사용 | 총 {total:.0f}초 ({total / 60:.1f}분)")
    dropped = [clip for clip in clips if not clip.usable]
    if dropped:
        print(f"제외 {len(dropped)}개 (전사 두 출처가 어긋남)")
        for clip in dropped[:5]:
            print(f"  {clip.name} {clip.agreement:.2f}")
            print(f"    기존: {clip.stored_text[:60]}")
            print(f"    다시: {clip.heard_text[:60]}")

    if not usable:
        return 1

    arguments.destination.mkdir(parents=True, exist_ok=True)
    speaker = arguments.speaker_id
    (arguments.destination / "wav.scp").write_text(
        "".join(f"{clip.name} {clip.path.resolve()}\n" for clip in usable), encoding="utf-8"
    )
    (arguments.destination / "text").write_text(
        "".join(f"{clip.name} {clip.heard_text}\n" for clip in usable), encoding="utf-8"
    )
    (arguments.destination / "utt2spk").write_text(
        "".join(f"{clip.name} {speaker}\n" for clip in usable), encoding="utf-8"
    )
    (arguments.destination / "spk2utt").write_text(
        f"{speaker} {' '.join(clip.name for clip in usable)}\n", encoding="utf-8"
    )
    (arguments.destination / "transcripts.json").write_text(
        json.dumps(
            [
                {
                    "name": clip.name,
                    "seconds": round(clip.seconds, 3),
                    "text": clip.heard_text,
                    "previous_text": clip.stored_text,
                    "agreement": round(clip.agreement, 3),
                    "used": clip.usable,
                }
                for clip in clips
            ],
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"저장: {arguments.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
