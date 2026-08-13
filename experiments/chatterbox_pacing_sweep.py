"""Find the settings at which Chatterbox speaks at the Reference's pace.

Her own recordings run at 2.80 syllables a second (3333 syllables over 1189
seconds of clips, median 3.00). Chatterbox at its defaults runs at 6.92 — two
and a half times her rate, which is what 천우 heard as "목소리는 완전
reference인데 속도가 다르다".

Upstream's tip is that lowering cfg_weight slows the pacing, so this sweeps it
and reports the rate for each setting. The model is loaded once: at 84 seconds a
load, a container per setting would spend all its time getting ready.

Rate is measured in Korean syllables per second, counting only Hangul, because
that is what the Reference's own numbers were measured in and the two have to be
comparable.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import wave

from chatterbox.mtl_tts import ChatterboxMultilingualTTS
import torch

LANGUAGE = "ko"
# Measured from 214 of her clips.
REFERENCE_SYLLABLES_PER_SECOND = 2.80


def syllables(text: str) -> int:
    return sum(1 for character in text if "가" <= character <= "힣")


def save(wav: torch.Tensor, rate: int, path: Path) -> float:
    samples = (wav.squeeze(0).clamp(-1, 1) * 32767).to(torch.int16).cpu().numpy()
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        handle.writeframes(samples.tobytes())
    path.chmod(0o600)
    return len(samples) / rate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--text", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--cfg-weights", type=float, nargs="+", default=[0.5, 0.3, 0.2])
    parser.add_argument("--exaggerations", type=float, nargs="+", default=[0.5])
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    model = ChatterboxMultilingualTTS.from_pretrained(device="cuda", t3_model="v3")
    count = syllables(arguments.text)
    print(f"목표 {REFERENCE_SYLLABLES_PER_SECOND:.2f} 음절/초 ({count}음절)")
    for weight in arguments.cfg_weights:
        for exaggeration in arguments.exaggerations:
            wav = model.generate(
                arguments.text,
                language_id=LANGUAGE,
                cfg_weight=weight,
                exaggeration=exaggeration,
            )
            path = arguments.output_dir / f"cfg{weight:g}-ex{exaggeration:g}.wav"
            seconds = save(wav, model.sr, path)
            print(
                f"  cfg {weight:g} ex {exaggeration:g}: {seconds:5.1f}초, "
                f"{count / seconds:.2f} 음절/초  → {path.name}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
