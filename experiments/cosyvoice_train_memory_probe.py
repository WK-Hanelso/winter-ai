"""Measure whether fine-tuning the flow model fits on this GPU.

The training script cannot be reached without satisfying deepspeed, which probes
for a CUDA compiler as it imports even when the chosen engine is plain torch,
and the pip-installable CUDA packages ship ptxas without nvcc. That is a
packaging fight, and it is not the question.

So this reads the checkpoint and nothing else. Parameter shapes are all the
measurement needs, and allocating tensors of those shapes on the GPU costs
exactly what the real parameters would. No CosyVoice module is imported, which
means no deepspeed, which means the number arrives today.

Weights, gradients and optimizer moments are allocated for real here. Activation
memory is not — a backward pass over actual audio would add to this, so whatever
this reports is a floor, and a comfortable-looking result would still need the
real run to confirm. An uncomfortable one is already an answer.

Both optimizers are measured: Adam keeps two fp32 moments per parameter, and its
8-bit form keeps two bytes instead of eight. On a card this size that difference
is most of the decision.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

import torch

GIB = 1024**3


def gibibytes(value: int) -> float:
    return value / GIB


def report(label: str) -> None:
    print(
        f"  {label:<22} 현재 {gibibytes(torch.cuda.memory_allocated()):>5.2f} GiB "
        f"| 최대 {gibibytes(torch.cuda.max_memory_allocated()):>5.2f} GiB"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument(
        "--optimizer",
        choices=("adam", "adam8bit"),
        default="adam",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    if not torch.cuda.is_available():
        print("GPU를 찾지 못했습니다.", file=sys.stderr)
        return 1

    name = torch.cuda.get_device_name(0)
    total = gibibytes(torch.cuda.get_device_properties(0).total_memory)
    print(f"{name} | {total:.2f} GiB")

    state = torch.load(str(arguments.checkpoint), map_location="cpu")
    tensors = [value for value in state.values() if torch.is_tensor(value)
               and value.is_floating_point()]
    count = sum(tensor.numel() for tensor in tensors)
    print(
        f"{arguments.checkpoint.name}: 학습 대상 파라미터 {count / 1e6:.1f}M "
        f"| optimizer {arguments.optimizer}"
    )

    torch.cuda.reset_peak_memory_stats()
    parameters = [
        torch.nn.Parameter(torch.empty_like(tensor, device="cuda")) for tensor in tensors
    ]
    report("가중치")

    if arguments.optimizer == "adam8bit":
        import bitsandbytes as bnb

        optimizer = bnb.optim.Adam8bit(parameters, lr=1e-5)
    else:
        optimizer = torch.optim.Adam(parameters, lr=1e-5)

    # A loss over the parameters themselves. It trains nothing, and that is the
    # point: it allocates exactly one gradient per parameter and one set of
    # optimizer moments, with no activations in the way.
    for parameter in parameters:
        parameter.grad = torch.zeros_like(parameter)
    report("+ 기울기")

    optimizer.step()
    report("+ optimizer 상태")

    peak = gibibytes(torch.cuda.max_memory_allocated())
    headroom = total - peak
    print(f"\n최대 {peak:.2f} GiB / {total:.2f} GiB | 활성값에 남는 여유 {headroom:.2f} GiB")
    if headroom < 0.5:
        print("여유가 거의 없습니다. 실제 학습은 활성값 때문에 더 필요합니다.")
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
