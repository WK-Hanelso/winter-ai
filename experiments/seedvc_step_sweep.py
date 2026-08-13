"""스테이지 2의 diffusion step 수를 훑어 같은 문장을 여러 번 변환한다.

지금 첫 소리 4~5초 중 3초가 목소리고, 그 절반이 여기다. step을 줄이면 빨라지는
것은 확실하지만 어디까지 줄여도 겨울이로 들리는지는 측정으로 정할 수 없다.
그래서 이 스크립트는 판단하지 않고 같은 문장을 step별로 만들어 둔다. 고르는
것은 천우의 귀다.

모델은 한 번만 올린다. 서버가 그렇게 하는 이유와 같다 — 로드가 변환보다 훨씬
비싸서, step마다 새로 올리면 재는 시간이 로드 시간에 묻힌다.
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
DEFAULT_STEPS = (30, 20, 15, 10, 6, 4)


def arguments_for(checkpoint: Path, config: Path, length_adjust: float) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        checkpoint=str(checkpoint),
        config=str(config),
        f0_condition=False,
        auto_f0_adjust=False,
        semi_tone_shift=0,
        diffusion_steps=DEFAULT_STEPS[0],
        length_adjust=length_adjust,
        inference_cfg_rate=INFERENCE_CFG_RATE,
        fp16=True,
        source="",
        target="",
        output="",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--length-adjust", type=float, default=1.15)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--steps", type=int, nargs="+", default=list(DEFAULT_STEPS))
    parser.add_argument("sources", type=Path, nargs="+", help="스테이지 1이 만든 wav")
    options = parser.parse_args()

    settings = arguments_for(options.checkpoint, options.config, options.length_adjust)
    started = time.perf_counter()
    loaded = inference.load_models(settings)
    inference.load_models = lambda _arguments: loaded
    print(f"모델 준비 {time.perf_counter() - started:.1f}초", flush=True)

    options.out.mkdir(parents=True, exist_ok=True)
    for source in options.sources:
        for steps in options.steps:
            settings.source = str(source)
            settings.target = str(options.reference)
            settings.diffusion_steps = steps
            with tempfile.TemporaryDirectory(prefix="winter-sweep-") as staging:
                settings.output = staging
                started = time.perf_counter()
                inference.main(settings)
                elapsed = time.perf_counter() - started
                produced = sorted(Path(staging).glob("*.wav"))
                if not produced:
                    print(f"  {source.stem} step {steps}: 결과 없음", flush=True)
                    continue
                destination = options.out / f"{source.stem}_step{steps:02d}.wav"
                shutil.copyfile(produced[0], destination)
            destination.chmod(0o600)
            print(f"  {source.stem} step {steps:>2}: {elapsed:.2f}초 -> {destination.name}", flush=True)


if __name__ == "__main__":
    main()
