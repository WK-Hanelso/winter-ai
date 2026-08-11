"""Container-side runner for Korean zero-shot voice cloning.

The repository ships ``GPT_SoVITS/inference_cli.py``, but its ``--ref_language``
only accepts Chinese, English and Japanese even though the engine supports
Korean. So this calls the same function that CLI calls, with the Korean label
the engine actually recognises.

Zero-shot means no training: the reference clip and its transcript are the whole
conditioning. The transcript matters as much as the audio — the model aligns
what it hears to what it is told was said, and a wrong transcript degrades the
voice rather than raising an error.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.append("/opt/gpt-sovits")
sys.path.append("/opt/gpt-sovits/GPT_SoVITS")

from GPT_SoVITS.inference_webui import (  # noqa: E402
    change_gpt_weights,
    change_sovits_weights,
    get_tts_wav,
)
import soundfile  # noqa: E402

PRETRAINED = Path("/opt/gpt-sovits/GPT_SoVITS/pretrained_models/gsv-v2final-pretrained")
GPT_WEIGHTS = PRETRAINED / "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt"
SOVITS_WEIGHTS = PRETRAINED / "s2G2333k.pth"
# The label the engine's internationalisation table uses for Korean.
KOREAN = "한문"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-audio", type=Path, required=True)
    parser.add_argument(
        "--reference-text",
        required=True,
        help="참조 음성에서 실제로 말한 내용. 틀리면 목소리 품질이 조용히 나빠집니다.",
    )
    parser.add_argument("--text", required=True, help="말하게 할 문장.")
    parser.add_argument("--output-path", type=Path, required=True)
    parser.add_argument("--language", default=KOREAN)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    change_gpt_weights(str(GPT_WEIGHTS))
    change_sovits_weights(str(SOVITS_WEIGHTS))

    # get_tts_wav yields (sample rate, samples) chunks rather than returning a
    # file, so the pieces are joined here.
    pieces = list(
        get_tts_wav(
            ref_wav_path=str(arguments.reference_audio),
            prompt_text=arguments.reference_text,
            prompt_language=arguments.language,
            text=arguments.text,
            text_language=arguments.language,
        )
    )
    if not pieces:
        print("합성 결과가 비었습니다.", file=sys.stderr)
        return 1

    rate = pieces[-1][0]
    import numpy as np

    audio = np.concatenate([piece[1] for piece in pieces])
    arguments.output_path.parent.mkdir(parents=True, exist_ok=True)
    soundfile.write(str(arguments.output_path), audio, rate)
    print(f"{arguments.output_path} ({len(audio) / rate:.1f}초, {rate} Hz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
