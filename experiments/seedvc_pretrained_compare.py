"""같은 소리를 우리 체크포인트와 파인튜닝 전 원본으로 각각 변환한다.

천우가 스테이지 2를 듣고 "데이터가 적어서 샘플이 부족한 느낌"이라고 했다.
그 말이 맞는지는 우리가 학습시킨 것과 학습시키지 않은 것을 같은 소리에
대보면 갈린다.

- 원본이 더 자연스러우면, 우리 체크포인트가 25.2분에 갇힌 것이다.
- 원본도 같이 이상하면, seed-vc가 낯선 스테이지 1 소리에 약한 것이다.

둘은 고치는 방법이 완전히 다르다. 앞이면 데이터를 늘리는 일이고, 뒤면
스테이지 1을 고르는 기준이 달라진다.

원본은 checkpoint를 주지 않으면 seed-vc가 알아서 받아온다. 참조 음성은 둘 다
같은 것을 쓴다 — 목소리를 정하는 것은 그쪽이다.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile
import time
import types

sys.path.append("/opt/seed-vc")

import inference  # noqa: E402

INFERENCE_CFG_RATE = 0.7


def settings_for(checkpoint: Path | None, config: Path | None,
                 length_adjust: float, steps: int) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        checkpoint=str(checkpoint) if checkpoint else None,
        config=str(config) if config else None,
        f0_condition=False, auto_f0_adjust=False, semi_tone_shift=0,
        diffusion_steps=steps, length_adjust=length_adjust,
        inference_cfg_rate=INFERENCE_CFG_RATE, fp16=True,
        source="", target="", output="",
    )


def convert_all(settings: types.SimpleNamespace, sources: list[Path],
                reference: Path, out: Path, tag: str) -> None:
    started = time.perf_counter()
    loaded = inference.load_models(settings)
    original = inference.load_models
    inference.load_models = lambda _a: loaded
    print(f"[{tag}] 모델 준비 {time.perf_counter() - started:.1f}초", flush=True)
    try:
        for source in sources:
            settings.source = str(source)
            settings.target = str(reference)
            with tempfile.TemporaryDirectory(prefix="winter-pre-") as staging:
                settings.output = staging
                elapsed = time.perf_counter()
                inference.main(settings)
                produced = sorted(Path(staging).glob("*.wav"))
                if not produced:
                    print(f"  {source.stem}: 결과 없음", flush=True)
                    continue
                destination = out / f"{source.stem}__{tag}.wav"
                shutil.copyfile(produced[0], destination)
            destination.chmod(0o600)
            print(f"  {source.stem} -> {destination.name} ({time.perf_counter()-elapsed:.2f}초)", flush=True)
    finally:
        inference.load_models = original


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--length-adjust", type=float, default=1.15)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("sources", type=Path, nargs="+")
    options = parser.parse_args()
    options.out.mkdir(parents=True, exist_ok=True)

    convert_all(settings_for(options.checkpoint, options.config,
                             options.length_adjust, options.steps),
                options.sources, options.reference, options.out, "우리것")
    convert_all(settings_for(None, None, options.length_adjust, options.steps),
                options.sources, options.reference, options.out, "원본")


if __name__ == "__main__":
    main()
