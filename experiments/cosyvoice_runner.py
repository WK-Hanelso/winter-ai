"""Container-side runner for Korean zero-shot cloning through CosyVoice 3.

Same shape as the GPT-SoVITS runner so the two can be compared directly: one
reference clip, its transcript, one sentence to say, one wav out.

The reference transcript matters as much as the audio — the model aligns what it
hears to what it is told was said. A wrong transcript degrades the result
without raising anything, which is how the first GPT-SoVITS attempt produced
slurred speech.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

sys.path.append("/opt/cosyvoice")
sys.path.append("/opt/cosyvoice/third_party/Matcha-TTS")

from cosyvoice.cli.cosyvoice import CosyVoice3  # noqa: E402
import torchaudio  # noqa: E402

MODEL_DIR = "/opt/cosyvoice/pretrained_models/CosyVoice3-0.5B"
# CosyVoice 3 reads the prompt as an instruction followed by the reference
# transcript, separated by this marker. The plain transcript alone is not the
# documented form.
INSTRUCT_PREFIX = "You are a helpful assistant.<|endofprompt|>"
# The reference clip has to be 16 kHz for the speaker encoder; anything else is
# resampled here rather than silently mis-read.
PROMPT_RATE = 16_000


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-audio", type=Path, required=True)
    parser.add_argument(
        "--reference-text",
        required=True,
        help="참조 음성에서 실제로 말한 내용. 틀리면 결과가 조용히 나빠집니다.",
    )
    parser.add_argument("--text", required=True, help="말하게 할 문장.")
    parser.add_argument("--output-path", type=Path, required=True)
    return parser


def prepare_prompt(path: Path, destination: Path) -> Path:
    """Write the reference as 16 kHz mono and return its path.

    A path, not a tensor: this version loads the prompt itself and rejects
    anything else with "Invalid file". The resample happens here rather than
    being left to the model so the rate is a stated fact, not a hope.
    """
    speech, rate = torchaudio.load(str(path))
    if speech.shape[0] > 1:
        speech = speech.mean(dim=0, keepdim=True)
    if rate != PROMPT_RATE:
        speech = torchaudio.transforms.Resample(rate, PROMPT_RATE)(speech)
    torchaudio.save(str(destination), speech, PROMPT_RATE)
    return destination


def main() -> int:
    arguments = build_parser().parse_args()
    # CosyVoice3 drops the load_jit argument its parent class takes.
    model = CosyVoice3(MODEL_DIR, load_trt=False, load_vllm=False, fp16=False)
    import tempfile

    staging = tempfile.mkdtemp(prefix="winter-prompt-")
    prompt = prepare_prompt(arguments.reference_audio, Path(staging) / "prompt16k.wav")

    import torchaudio as _ta

    checked, checked_rate = _ta.load(str(prompt))
    print(f"참조: {checked.shape[1] / checked_rate:.2f}초, {checked_rate} Hz, {checked.shape}")

    pieces = [
        result["tts_speech"]
        for result in model.inference_zero_shot(
            arguments.text,
            INSTRUCT_PREFIX + arguments.reference_text,
            str(prompt),
            stream=False,
        )
    ]
    if not pieces:
        print("합성 결과가 비었습니다.", file=sys.stderr)
        return 1

    import torch

    audio = torch.cat(pieces, dim=1)
    arguments.output_path.parent.mkdir(parents=True, exist_ok=True)
    torchaudio.save(str(arguments.output_path), audio, model.sample_rate)
    seconds = audio.shape[1] / model.sample_rate
    print(f"{arguments.output_path} ({seconds:.1f}초, {model.sample_rate} Hz)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
