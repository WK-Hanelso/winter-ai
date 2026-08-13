"""소스의 음높이를 조건으로 주는 경로를 켜고, 억양이 살아남는지 본다.

측정된 문제: 스테이지 1은 평서문과 의문문을 끝 기울기 12.1반음 차이로 구별해
말하는데, 변환을 거치면 1.1반음으로 줄어든다. 음색은 참조로 옮겨가지만 억양은
옮겨가는 것이 아니라 눌린다.

seed-vc에는 그 자리를 위한 경로가 따로 있다. f0_condition을 켜면 44k f0 모델과
RMVPE가 붙어 소스의 음높이 곡선을 조건으로 받는다. 파인튜닝한 체크포인트는
f0 없는 모델용이라 여기 쓸 수 없지만, 천우가 이미 원본 쪽이 자연스럽다고
판단했으므로 원본으로 간다.

auto_f0_adjust는 소스의 음역을 참조 화자에게 맞출지의 값이다. 끄면 소스의
높이가 남고 켜면 참조의 높이로 옮겨지므로, 어느 쪽이 겨울이로 들리는지는
들어봐야 안다. 둘 다 만든다.
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


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--length-adjust", type=float, default=1.15)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("sources", type=Path, nargs="+")
    options = parser.parse_args()
    options.out.mkdir(parents=True, exist_ok=True)

    for auto_adjust in (True, False):
        settings = types.SimpleNamespace(
            checkpoint=None, config=None,
            f0_condition=True, auto_f0_adjust=auto_adjust, semi_tone_shift=0,
            diffusion_steps=options.steps, length_adjust=options.length_adjust,
            inference_cfg_rate=INFERENCE_CFG_RATE, fp16=True,
            source="", target="", output="",
        )
        tag = "f0맞춤" if auto_adjust else "f0그대로"
        started = time.perf_counter()
        loaded = inference.load_models(settings)
        original = inference.load_models
        inference.load_models = lambda _a: loaded
        print(f"[{tag}] 모델 준비 {time.perf_counter() - started:.1f}초", flush=True)
        try:
            for source in options.sources:
                settings.source = str(source)
                settings.target = str(options.reference)
                with tempfile.TemporaryDirectory(prefix="winter-f0-") as staging:
                    settings.output = staging
                    at = time.perf_counter()
                    inference.main(settings)
                    produced = sorted(Path(staging).glob("*.wav"))
                    if not produced:
                        print(f"  {source.stem}: 결과 없음", flush=True)
                        continue
                    destination = options.out / f"{source.stem}__{tag}.wav"
                    shutil.copyfile(produced[0], destination)
                destination.chmod(0o600)
                print(f"  {source.stem} -> {destination.name} ({time.perf_counter()-at:.2f}초)", flush=True)
        finally:
            inference.load_models = original


if __name__ == "__main__":
    main()
