"""Compare Chatterbox's built-in voice with direct Reference conditioning.

This is intentionally an inference-only probe.  The current production path
uses Chatterbox without ``audio_prompt_path`` and asks Seed-VC to add speaker
identity afterwards.  Chatterbox V3 can instead condition generation on the
Reference itself, which may preserve natural prosody without a second model
rebuilding the audio.

The direct prompt changes three things at once: T3 speaker identity, T3 prompt
speech tokens, and S3Gen acoustic conditioning. The hybrid outputs vary those
independently. That matters when the Reference is the right person but the
chosen six-second prompt has flatter delivery than Chatterbox's built-in voice.
"""

from __future__ import annotations

import argparse
import copy
from pathlib import Path
import re
import time
from typing import Any

LANGUAGE = "ko"
MODEL_VERSION = "v3"
DEFAULT_CFG_WEIGHT = 0.3
DEFAULT_EXAGGERATION = 0.5
BASE_SEED = 240814
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


def set_seed(seed: int) -> None:
    import torch

    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def synthesize_set(
    model: Any,
    sentences: list[tuple[str, str]],
    output_dir: Path,
    suffix: str,
    cfg_weight: float,
    exaggeration: float,
) -> None:
    import torchaudio

    for index, (name, text) in enumerate(sentences):
        set_seed(BASE_SEED + index)
        started = time.perf_counter()
        wav = model.generate(
            text,
            language_id=LANGUAGE,
            cfg_weight=cfg_weight,
            exaggeration=exaggeration,
        )
        destination = output_dir / f"{name}{suffix}.wav"
        torchaudio.save(str(destination), wav.cpu(), model.sr)
        destination.chmod(0o600)
        print(f"  {name}: {destination.name} ({time.perf_counter() - started:.1f}초)", flush=True)


def main() -> None:
    from chatterbox.mtl_tts import ChatterboxMultilingualTTS

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--sentences", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--cfg-weight", type=float, default=DEFAULT_CFG_WEIGHT)
    parser.add_argument("--exaggeration", type=float, default=DEFAULT_EXAGGERATION)
    options = parser.parse_args()

    if not options.reference.is_file():
        raise SystemExit(f"참조 음성이 없습니다: {options.reference}")
    sentences = load_sentences(options.sentences)
    options.out.mkdir(parents=True, exist_ok=True)

    started = time.perf_counter()
    model = ChatterboxMultilingualTTS.from_pretrained(device="cuda", t3_model=MODEL_VERSION)
    print(f"Chatterbox 준비됨 ({time.perf_counter() - started:.1f}초)", flush=True)
    default_conditions = copy.deepcopy(model.conds)

    # Do this first. prepare_conditionals mutates the model's active speaker.
    print("[A] Chatterbox 기본 음성", flush=True)
    synthesize_set(
        model, sentences, options.out, "", options.cfg_weight, options.exaggeration
    )

    print("[B] Chatterbox Reference 직접 조건", flush=True)
    model.prepare_conditionals(str(options.reference), exaggeration=options.exaggeration)
    # Some tensors produced by the encoders are not graph leaves, and PyTorch
    # deliberately refuses to deepcopy them. They are inference-only and are
    # never mutated below, so retain this condition object and only copy the
    # destination objects into which its tensors are placed.
    reference_conditions = model.conds
    synthesize_set(
        model, sentences, options.out, "__직접", options.cfg_weight, options.exaggeration
    )

    # Keep the built-in prompt tokens, which drive T3's delivery, while moving
    # identity through one or both of the two speaker-conditioning paths.
    mixed = copy.deepcopy(default_conditions)
    mixed.t3.speaker_emb = reference_conditions.t3.speaker_emb
    mixed.gen = reference_conditions.gen
    model.conds = mixed
    print("[C] 기본 억양 + Reference T3/S3Gen 음색", flush=True)
    synthesize_set(
        model, sentences, options.out, "__혼합-음색", options.cfg_weight, options.exaggeration
    )

    t3_only = copy.deepcopy(default_conditions)
    t3_only.t3.speaker_emb = reference_conditions.t3.speaker_emb
    model.conds = t3_only
    print("[D] 기본 억양 + Reference T3 화자 임베딩", flush=True)
    synthesize_set(
        model, sentences, options.out, "__혼합-t3", options.cfg_weight, options.exaggeration
    )

    gen_only = copy.deepcopy(default_conditions)
    gen_only.gen = reference_conditions.gen
    model.conds = gen_only
    print("[E] 기본 억양 + Reference S3Gen 음향 조건", flush=True)
    synthesize_set(
        model, sentences, options.out, "__혼합-gen", options.cfg_weight, options.exaggeration
    )


if __name__ == "__main__":
    main()
