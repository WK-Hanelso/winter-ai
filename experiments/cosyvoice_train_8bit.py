"""Run CosyVoice's trainer inside 6 GiB: 8-bit optimizer, and frozen early layers.

Its config accepts ``adam`` or ``adamw`` and nothing else, and 32-bit Adam does
not fit here: measured on this card, weights and gradients and moments come to
more than 5.79 GiB and the run dies before a step. The 8-bit form keeps two
bytes per parameter instead of eight, which brought the same measurement to
3.11 GiB with 2.68 GiB left for activations.

So ``torch.optim.Adam`` is replaced before the trainer is imported. The trainer
looks the name up on the module when it builds the optimizer, so it gets this
one without knowing. Nothing upstream is edited — a patched checkout would have
to be re-patched at every version bump, and the substitution would stop being
visible from the command that ran it.

That alone was not enough. With it, sixty batches ran at 5.9 GiB of 6.0 GiB
before dying on a 2 MiB allocation: the remaining pressure is activations, and a
smaller batch cannot help once a batch already holds one sample.

So the first half of the flow decoder's 22 transformer blocks is frozen. The
saving is not mainly their gradients and optimizer state — it is that no
gradient flows behind the first trainable block, so the activations of every
block before it are never stored. Freezing the *early* layers is what buys the
memory; freezing late ones would buy far less.

It also suits the task. Adapting a speaker is a matter of how the model renders
a voice rather than what it understands of the input, and the later blocks are
where that lives.

Everything else is the trainer's own: this passes its arguments through
untouched.
"""

from __future__ import annotations

import sys

import bitsandbytes as bnb
import torch.optim

# Before the import below, not after: train_utils resolves optim.Adam when it
# builds the optimizer, so the name has to already point here by then.
torch.optim.Adam = bnb.optim.Adam8bit
torch.optim.AdamW = bnb.optim.AdamW8bit

from cosyvoice.bin import train as train_module  # noqa: E402
from cosyvoice.bin.train import main  # noqa: E402

# Of 22 blocks. Chosen because it halves the trainable depth; if the run still
# does not fit, this is the number to raise.
FROZEN_BLOCKS = 11


def freeze_early_blocks(model: torch.nn.Module) -> None:
    frozen = 0
    kept = 0
    for name, parameter in model.named_parameters():
        index = _block_index(name)
        if index is not None and index < FROZEN_BLOCKS:
            parameter.requires_grad = False
            frozen += parameter.numel()
        else:
            kept += parameter.numel()
    print(
        f"frozen: {frozen / 1e6:.1f}M parameters in blocks 0-{FROZEN_BLOCKS - 1} | "
        f"trainable: {kept / 1e6:.1f}M",
        file=sys.stderr,
    )


def _block_index(name: str) -> int | None:
    parts = name.split(".")
    for position, part in enumerate(parts):
        if part.endswith("blocks") and position + 1 < len(parts):
            following = parts[position + 1]
            return int(following) if following.isdigit() else None
    return None


def main_with_frozen_blocks() -> None:
    """Freeze before the optimizer is built, so it never sees the frozen half.

    Patched on ``cosyvoice.bin.train``, not on the module the function is
    defined in. train.py does ``from ... import init_optimizer_and_scheduler``,
    which copies the function into its own namespace at import time, so
    replacing the original afterwards changes nothing — the first attempt at
    this silently did not freeze anything and failed at the same batch with the
    same losses. The optimizer substitution above works the other way round
    because ``optim.Adam`` is an attribute lookup made when it is called.
    """
    original = train_module.init_optimizer_and_scheduler

    def patched(args, configs, model, gan):  # type: ignore[no-untyped-def]
        freeze_early_blocks(model)
        return original(args, configs, model, gan)

    train_module.init_optimizer_and_scheduler = patched
    main()


if __name__ == "__main__":
    print(f"optimizer: {torch.optim.Adam.__name__} (8-bit)", file=sys.stderr)
    main_with_frozen_blocks()
