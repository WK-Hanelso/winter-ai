"""같은 문장을 여러 체크포인트로 변환하고, 억양이 얼마나 살아남는지 잰다.

측정된 문제는 이것이다. 스테이지 1은 평서문과 의문문을 문장 끝 기울기 12.1반음
차이로 구별해 말하는데, 스테이지 2를 거치면 1.1반음으로 줄어든다. 음색은
참조로 옮겨가지만 억양은 옮겨가는 것이 아니라 눌린다.

학습 데이터를 25.2분에서 89.4분으로 늘린 것이 그 숫자를 얼마나 되돌리는지가
이 스크립트가 답하는 질문이다. 귀로도 듣지만, 귀만으로는 "고정된 느낌"이
평서문과 의문문이 닮아진 것인지 각각이 밋밋해진 것인지 가릴 수 없었다.

체크포인트를 주지 않으면 파인튜닝 전 원본이 쓰인다. 그것도 같이 재는 이유는,
파인튜닝이 값을 하는지가 아직 확정되지 않았기 때문이다.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import shutil
import sys
import tempfile
import types

sys.path.append("/opt/seed-vc")

import inference  # noqa: E402

INFERENCE_CFG_RATE = 0.7


def convert(label: str, checkpoint: Path | None, config: Path | None,
            sources: list[Path], reference: Path, out: Path,
            length_adjust: float, steps: int) -> None:
    settings = types.SimpleNamespace(
        checkpoint=str(checkpoint) if checkpoint else None,
        config=str(config) if config else None,
        f0_condition=False, auto_f0_adjust=False, semi_tone_shift=0,
        diffusion_steps=steps, length_adjust=length_adjust,
        inference_cfg_rate=INFERENCE_CFG_RATE, fp16=True,
        source="", target="", output="",
    )
    loaded = inference.load_models(settings)
    original = inference.load_models
    inference.load_models = lambda _a: loaded
    print(f"[{label}] 모델 준비됨", flush=True)
    try:
        for source in sources:
            settings.source = str(source)
            settings.target = str(reference)
            with tempfile.TemporaryDirectory(prefix="winter-eval-") as staging:
                settings.output = staging
                inference.main(settings)
                produced = sorted(Path(staging).glob("*.wav"))
                if not produced:
                    print(f"  {source.stem}: 결과 없음", flush=True)
                    continue
                destination = out / f"{source.stem}__{label}.wav"
                shutil.copyfile(produced[0], destination)
            destination.chmod(0o600)
            print(f"  {source.stem} -> {destination.name}", flush=True)
    finally:
        inference.load_models = original


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--length-adjust", type=float, default=1.15)
    parser.add_argument("--steps", type=int, default=30)
    parser.add_argument(
        "--checkpoint",
        action="append",
        default=[],
        help="라벨=체크포인트=설정. 생략하면 파인튜닝 전 원본만 잰다.",
    )
    parser.add_argument("sources", type=Path, nargs="+")
    options = parser.parse_args()
    options.out.mkdir(parents=True, exist_ok=True)

    convert("원본", None, None, options.sources, options.reference,
            options.out, options.length_adjust, options.steps)
    for entry in options.checkpoint:
        label, checkpoint, config = entry.split("=", 2)
        convert(label, Path(checkpoint), Path(config), options.sources,
                options.reference, options.out, options.length_adjust, options.steps)


if __name__ == "__main__":
    main()
