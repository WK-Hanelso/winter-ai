"""파인튜닝 없이, 참조 음성만 바꿔가며 같은 소리를 변환한다.

천우가 파인튜닝한 체크포인트보다 원본이 자연스럽다고 판단했다. 원본은 참조
음성만으로 목소리를 정하므로, 참조를 잘 고르면 파인튜닝 없이 갈 수 있다.
그러면 학습 데이터가 25.2분뿐이라는 문제 자체가 사라진다.

바꾸는 것은 참조 하나뿐이다. 소스도 길이도 step도 같게 두어야, 들리는 차이가
참조에서 왔다고 말할 수 있다.
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
    parser.add_argument("--references", type=Path, nargs="+", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--length-adjust", type=float, default=1.15)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument("sources", type=Path, nargs="+")
    options = parser.parse_args()

    settings = types.SimpleNamespace(
        checkpoint=None, config=None,
        f0_condition=False, auto_f0_adjust=False, semi_tone_shift=0,
        diffusion_steps=options.steps, length_adjust=options.length_adjust,
        inference_cfg_rate=INFERENCE_CFG_RATE, fp16=True,
        source="", target="", output="",
    )
    started = time.perf_counter()
    loaded = inference.load_models(settings)
    inference.load_models = lambda _a: loaded
    print(f"원본 모델 준비 {time.perf_counter() - started:.1f}초", flush=True)

    options.out.mkdir(parents=True, exist_ok=True)
    for source in options.sources:
        for reference in options.references:
            settings.source = str(source)
            settings.target = str(reference)
            with tempfile.TemporaryDirectory(prefix="winter-ref-") as staging:
                settings.output = staging
                inference.main(settings)
                produced = sorted(Path(staging).glob("*.wav"))
                if not produced:
                    print(f"  {source.stem} / {reference.stem}: 결과 없음", flush=True)
                    continue
                destination = options.out / f"{source.stem}__{reference.stem}.wav"
                shutil.copyfile(produced[0], destination)
            destination.chmod(0o600)
            print(f"  {source.stem} / {reference.stem} -> {destination.name}", flush=True)


if __name__ == "__main__":
    main()
