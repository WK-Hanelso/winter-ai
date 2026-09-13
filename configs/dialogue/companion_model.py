"""Local text draft sampling; runtime/model deployment remains separate."""

# 2026-09-13: 0.0 let a long session copy its own previous reply nearly verbatim
# (turns 500->512, 502->504). Qwen3 card range 0~2; 1.0 matches the legacy adapter default.
PRESENCE_PENALTY = 1.0
MIN_P = 0.0
