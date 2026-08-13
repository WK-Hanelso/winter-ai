"""같은 소리를 length_adjust만 바꿔 변환한다.

1.15는 Chatterbox를 위해 고른 값이다. Chatterbox가 초당 6.45음절로 말하고
Reference는 2.80이라, 늘여서 맞춘 것이었다. 스테이지 1을 바꾸면 그 전제가
사라지므로, 새 스테이지 1에 맞는 값은 다시 골라야 한다. 이미 한국어 속도로
읽는 소리를 또 늘이면 억양이 통째로 늘어난다.

여기서도 고르지 않는다. 만들어만 두고 천우가 듣는다.
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
DIFFUSION_STEPS = 30
DEFAULT_LENGTHS = (1.0, 1.05, 1.15, 1.3)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--lengths", type=float, nargs="+", default=list(DEFAULT_LENGTHS))
    parser.add_argument("--steps", type=int, default=DIFFUSION_STEPS)
    parser.add_argument("sources", type=Path, nargs="+")
    options = parser.parse_args()

    settings = types.SimpleNamespace(
        checkpoint=str(options.checkpoint), config=str(options.config),
        f0_condition=False, auto_f0_adjust=False, semi_tone_shift=0,
        diffusion_steps=options.steps, length_adjust=DEFAULT_LENGTHS[0],
        inference_cfg_rate=INFERENCE_CFG_RATE, fp16=True,
        source="", target="", output="",
    )
    started = time.perf_counter()
    loaded = inference.load_models(settings)
    inference.load_models = lambda _a: loaded
    print(f"모델 준비 {time.perf_counter() - started:.1f}초", flush=True)

    options.out.mkdir(parents=True, exist_ok=True)
    for source in options.sources:
        for length in options.lengths:
            settings.source = str(source)
            settings.target = str(options.reference)
            settings.length_adjust = length
            with tempfile.TemporaryDirectory(prefix="winter-len-") as staging:
                settings.output = staging
                inference.main(settings)
                produced = sorted(Path(staging).glob("*.wav"))
                if not produced:
                    print(f"  {source.stem} len {length}: 결과 없음", flush=True)
                    continue
                destination = options.out / f"{source.stem}_len{length:g}.wav".replace(".", "_", 1)
                destination = options.out / f"{source.stem}_len{str(length).replace('.','p')}.wav"
                shutil.copyfile(produced[0], destination)
            destination.chmod(0o600)
            print(f"  {source.stem} len {length:g} -> {destination.name}", flush=True)


if __name__ == "__main__":
    main()
