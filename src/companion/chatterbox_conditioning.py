"""Select a measured Chatterbox speaker condition without importing its runtime."""

from __future__ import annotations

import copy
from typing import Any

CONDITIONING_MODES = ("builtin", "s3gen-reference")


def select_conditioning(default: Any, reference: Any, mode: str) -> Any:
    """Build one serving condition without mutating either measured baseline."""
    if mode == "builtin":
        return copy.deepcopy(default)
    if mode == "s3gen-reference":
        selected = copy.deepcopy(default)
        # Keep Chatterbox's T3 prompt/delivery. Only the S3Gen acoustic
        # condition comes from the Reference; this was 천우's highest-ranked
        # path in the blind final and avoids a second conversion model.
        selected.gen = reference.gen
        return selected
    raise ValueError(f"모르는 Chatterbox 조건입니다: {mode}")
