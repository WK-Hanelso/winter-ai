"""Generate the voice bake-off sentences with direct Qwen3-TTS cloning.

The rejected path first makes a natural generic voice and then asks a separate
VC model to rebuild it as the Reference. Listening showed the unavoidable
trade: explicit F0 kept the question contour but added rough artifacts, while
the smoother variants flattened that contour and still did not sound enough
like the Reference.

Qwen3-TTS Base is tested because it emits the final cloned waveform itself. It
has no intermediate waveform for a converter to damage. Two official modes are
kept separate:

* ``icl`` conditions on reference acoustic codes and their exact transcript;
* ``embedding`` uses only the speaker embedding, which may clone less closely
  but cannot leak the end of the reference transcript into the answer.

This is inference-only. Fine-tuning is considered only if one of these outputs
is already a plausible direction to 천우's ear.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import time
from typing import Any

import numpy as np

MODEL = "Qwen/Qwen3-TTS-12Hz-0.6B-Base"
LANGUAGE = "Korean"
BASE_SEED = 240814
MODES = ("icl", "embedding")
DTYPES = ("auto", "float16", "bfloat16", "float32")
VALID_ID = re.compile(r"^[a-z][a-z0-9_-]*$")


def load_sentences(path: Path) -> list[tuple[str, str]]:
    sentences: list[tuple[str, str]] = []
    seen: set[str] = set()
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            name, text = line.split("\t", 1)
        except ValueError as error:
            raise ValueError(f"{path}:{line_number}: ID와 문장을 탭으로 구분해야 합니다") from error
        name, text = name.strip(), text.strip()
        if not VALID_ID.fullmatch(name):
            raise ValueError(f"{path}:{line_number}: 안전하지 않은 ID입니다: {name!r}")
        if name in seen:
            raise ValueError(f"{path}:{line_number}: ID가 중복됩니다: {name}")
        if not text:
            raise ValueError(f"{path}:{line_number}: 문장이 비었습니다")
        seen.add(name)
        sentences.append((name, text))
    if not sentences:
        raise ValueError(f"문장이 없습니다: {path}")
    return sentences


def load_reference_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError(f"참조 대사가 비었습니다: {path}")
    return text


def selected_modes(values: list[str] | None) -> tuple[str, ...]:
    if not values:
        return MODES
    return tuple(dict.fromkeys(values))


def selected_dtype(requested: str, cuda_capability: tuple[int, int]) -> str:
    """Choose a numerically safe dtype for the installed GPU.

    Qwen3-TTS FP16 generation is known to overflow on Turing GPUs such as the
    RTX 2060. Those cards also lack native BF16, so FP32 is the safe automatic
    choice. Ampere and newer cards use the project's recommended BF16 path.
    """
    if requested != "auto":
        return requested
    return "bfloat16" if cuda_capability[0] >= 8 else "float32"


def save_audio(samples: Any, sample_rate: int, destination: Path) -> None:
    audio = np.asarray(samples, dtype=np.float32).squeeze()
    if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
        raise ValueError(f"유효하지 않은 합성 음성입니다: shape={audio.shape}")
    import soundfile as sf

    sf.write(str(destination), audio, sample_rate, subtype="PCM_16")
    destination.chmod(0o600)


def main() -> None:
    from qwen_tts import Qwen3TTSModel
    import torch

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--reference-text", type=Path, required=True)
    parser.add_argument("--sentences", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--mode", choices=MODES, action="append")
    parser.add_argument("--dtype", choices=DTYPES, default="auto")
    options = parser.parse_args()

    for required in (options.reference, options.reference_text, options.sentences):
        if not required.is_file():
            raise SystemExit(f"필요한 파일이 없습니다: {required}")
    reference_text = load_reference_text(options.reference_text)
    sentences = load_sentences(options.sentences)
    modes = selected_modes(options.mode)
    options.out.mkdir(parents=True, exist_ok=True)

    dtype_name = selected_dtype(options.dtype, torch.cuda.get_device_capability(0))
    dtype = getattr(torch, dtype_name)
    print(f"dtype={dtype_name}", flush=True)
    started = time.perf_counter()
    model = Qwen3TTSModel.from_pretrained(
        options.model,
        device_map="cuda:0",
        dtype=dtype,
        attn_implementation="sdpa",
    )
    print(f"Qwen3-TTS 준비됨 ({time.perf_counter() - started:.1f}초)", flush=True)

    for mode in modes:
        embedding_only = mode == "embedding"
        prompt = model.create_voice_clone_prompt(
            ref_audio=str(options.reference),
            ref_text=None if embedding_only else reference_text,
            x_vector_only_mode=embedding_only,
        )
        print(f"[{mode}]", flush=True)
        for index, (name, text) in enumerate(sentences):
            torch.manual_seed(BASE_SEED + index)
            if torch.cuda.is_available():
                torch.cuda.manual_seed_all(BASE_SEED + index)
            generated_at = time.perf_counter()
            wavs, sample_rate = model.generate_voice_clone(
                text=text,
                language=LANGUAGE,
                voice_clone_prompt=prompt,
                non_streaming_mode=True,
            )
            destination = options.out / f"{name}__qwen-{mode}.wav"
            save_audio(wavs[0], sample_rate, destination)
            elapsed = time.perf_counter() - generated_at
            print(f"  {name}: {destination.name} ({elapsed:.1f}초)", flush=True)


if __name__ == "__main__":
    main()
