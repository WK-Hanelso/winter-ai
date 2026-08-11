"""Run CosyVoice's trainer with an 8-bit optimizer.

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

Everything else is the trainer's own: this passes its arguments through
untouched.
"""

from __future__ import annotations

import sys

import bitsandbytes as bnb
import torch.optim

# Before the import below, not after: train_utils resolves optim.Adam when it
# builds the optimizer, so the name has to already point here by then.
torch.optim.Adam = bnb.optim.Adam8bit  # type: ignore[misc,assignment]
torch.optim.AdamW = bnb.optim.AdamW8bit  # type: ignore[misc,assignment]

from cosyvoice.bin.train import main  # noqa: E402

if __name__ == "__main__":
    print(f"optimizer: {torch.optim.Adam.__name__} (8-bit)", file=sys.stderr)
    main()
