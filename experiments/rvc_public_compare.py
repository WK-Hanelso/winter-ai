"""Convert the fixed voice-path sentences with a public RVC v2 model.

This probe answers one narrow question: do the public RVC models behind many
AI covers outperform our Seed-VC and Chatterbox-reference paths when every
candidate receives the exact same spoken source? It does not treat a public
figure model as Winter's final identity. Outputs remain private, local review
artifacts and the model itself remains outside Git.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import time
from typing import Any

OUTPUT_SUFFIX = "__rvc-public"


def source_files(directory: Path) -> list[Path]:
    sources = sorted(directory.glob("[sq][0-9].wav"))
    if not sources:
        raise ValueError(f"s1.wav/q1.wav 형식의 소스가 없습니다: {directory}")
    return sources


def destination_for(source: Path, output: Path) -> Path:
    return output / f"{source.stem}{OUTPUT_SUFFIX}.wav"


def main() -> None:
    from rvc.infer.infer import VoiceConverter

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pitch", type=int, default=0)
    parser.add_argument("--index-rate", type=float, default=0.75)
    parser.add_argument("--protect", type=float, default=0.33)
    options = parser.parse_args()

    for required in (options.model, options.index):
        if not required.is_file():
            raise SystemExit(f"필요한 RVC 파일이 없습니다: {required}")
    if not 0 <= options.index_rate <= 1:
        raise SystemExit("--index-rate는 0에서 1 사이여야 합니다")
    if not 0 <= options.protect <= 0.5:
        raise SystemExit("--protect는 0에서 0.5 사이여야 합니다")

    sources = source_files(options.sources)
    options.out.mkdir(parents=True, exist_ok=True)
    converter: Any = VoiceConverter()
    started = time.perf_counter()

    for source in sources:
        destination = destination_for(source, options.out)
        converted_at = time.perf_counter()
        converter.convert_audio(
            audio_input_path=str(source),
            audio_output_path=str(destination),
            model_path=str(options.model),
            index_path=str(options.index),
            pitch=options.pitch,
            f0_method="rmvpe",
            index_rate=options.index_rate,
            volume_envelope=1.0,
            protect=options.protect,
            split_audio=False,
            f0_autotune=False,
            clean_audio=False,
            export_format="WAV",
            embedder_model="contentvec",
        )
        if not destination.is_file() or destination.stat().st_size <= 44:
            raise RuntimeError(f"RVC 결과가 생성되지 않았습니다: {destination}")
        destination.chmod(0o600)
        print(f"{destination.name}: {time.perf_counter() - converted_at:.2f}초", flush=True)

    print(f"전체 {len(sources)}개: {time.perf_counter() - started:.2f}초", flush=True)


if __name__ == "__main__":
    main()
