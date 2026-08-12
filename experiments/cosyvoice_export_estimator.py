"""Export the fine-tuned flow estimator to ONNX, for a TensorRT engine.

``load_trt`` deletes ``flow.decoder.estimator`` and rebuilds it from an ONNX
file. The one shipped in the model directory was exported from the pretrained
weights, so turning TensorRT on without this step would quietly discard the
fine-tuning and serve a voice that is not hers — succeeding, sounding wrong, and
giving no reason why.

The repository's own exporter loads the model directory and exports whatever is
in it. This loads the trained checkpoint over that first, and refuses to write
anything if loading it changed no weights.

The estimator is where the training went: 331 M of the flow's 332 M parameters.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

sys.path.append("/opt/cosyvoice")
sys.path.append("/opt/cosyvoice/third_party/Matcha-TTS")

from cosyvoice.cli.cosyvoice import CosyVoice3  # noqa: E402
import torch  # noqa: E402

MODEL_DIR = "/opt/cosyvoice/pretrained_models/CosyVoice3-0.5B"
# The shapes the estimator is called with, taken from the model's own
# get_trt_kwargs so the exported graph matches what TensorRT will be given.
INPUT_NAMES = ["x", "mask", "mu", "t", "spks", "cond"]


def load_trained_flow(model: CosyVoice3, checkpoint: Path) -> None:
    state = torch.load(str(checkpoint), map_location="cpu")
    state = state.get("model", state)
    weights = {name: value for name, value in state.items() if torch.is_tensor(value)}
    current = model.model.flow.state_dict()
    before = {name: tensor.clone() for name, tensor in current.items()}
    model.model.flow.load_state_dict(weights, strict=False)
    after = model.model.flow.state_dict()
    changed = sum(
        1 for name, tensor in after.items()
        if name in before and not torch.equal(before[name], tensor)
    )
    if changed == 0:
        raise SystemExit("학습된 flow를 불러왔지만 가중치가 하나도 바뀌지 않았습니다.")
    print(f"flow: {changed}/{len(after)} 텐서가 바뀜")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flow-checkpoint", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--frames", type=int, default=500, help="내보낼 때 쓰는 예시 길이.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    model = CosyVoice3(MODEL_DIR, load_trt=False, load_vllm=False, fp16=False)
    load_trained_flow(model, arguments.flow_checkpoint)

    estimator = model.model.flow.decoder.estimator
    estimator.eval()
    device = next(estimator.parameters()).device
    channels = model.model.flow.decoder.estimator.in_channels // 2
    frames = arguments.frames

    example = (
        torch.rand((2, channels, frames), device=device),
        torch.ones((2, 1, frames), device=device),
        torch.rand((2, channels, frames), device=device),
        torch.rand((2,), device=device),
        torch.rand((2, 80), device=device),
        torch.rand((2, channels, frames), device=device),
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    torch.onnx.export(
        estimator,
        example,
        str(arguments.output),
        export_params=True,
        opset_version=18,
        do_constant_folding=True,
        input_names=INPUT_NAMES,
        output_names=["estimator_out"],
        dynamic_axes={
            "x": {0: "batch_size", 2: "seq_len"},
            "mask": {0: "batch_size", 2: "seq_len"},
            "mu": {0: "batch_size", 2: "seq_len"},
            "cond": {0: "batch_size", 2: "seq_len"},
            "t": {0: "batch_size"},
            "spks": {0: "batch_size"},
            "estimator_out": {0: "batch_size", 2: "seq_len"},
        },
    )
    arguments.output.chmod(0o600)
    size = arguments.output.stat().st_size / 1024**2
    print(f"{arguments.output} ({size:.0f} MiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
