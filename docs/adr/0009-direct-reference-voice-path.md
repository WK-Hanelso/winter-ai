# ADR-0009 — Operate the direct S3Gen Reference voice path

- Status: accepted for limited phone operation
- Date: 2026-08-17

## Context

The phone voice used two serial GPU models for its first sentence:
Chatterbox produced Korean speech and a fine-tuned Seed-VC converted its timbre.
For the same fixed sentence the measured HTTP times were 2.16 seconds and 2.66
seconds, so the first playable audio waited 4.82 seconds after text was ready.

This was not only a speed problem. In the previous blind final, 천우 ranked the
Chatterbox S3Gen-only Reference condition first and the current fine-tuned
Seed-VC second. He also judged every candidate below the final voice-quality
target, so that ranking is a relative operating choice rather than acceptance
of a finished Winter voice.

## Decision

For limited phone operation, Chatterbox keeps its built-in T3 delivery while
using only the selected Reference S3Gen acoustic condition. Web passes the
result through directly and does not invoke Seed-VC. The old two-stage path and
its checkpoint remain available as an explicit rollback; there is no silent
runtime fallback.

The Chatterbox container receives only the selected prompt WAV as a read-only
mount, not the whole private Reference storage. The Web latency database is
versioned again as `data/voice_latency-sensevoice-direct.sqlite` so prior
Whisper and two-stage observations remain intact.

## Evidence

- Same fixed sentence, old two-stage path: 4.82 seconds total.
- Same fixed sentence, direct S3Gen warm runs: 2.21 and 2.27 seconds.
- Isolated real Core probe: first LLM sentence at 0.95 seconds and first direct
  audio ready at 2.4 seconds from turn start.
- Local STT retained the intended content on two of three direct generations;
  one repeated a phrase. The two-stage baseline also produced substitutions,
  so content fidelity remains a shared open gate.
- The earlier six-sentence blind final ranked direct S3Gen above fine-tuned
  Seed-VC, while judging both insufficient as a final identity.

## Rollback

1. Recreate `compose.chatterbox.yaml` to restore built-in Chatterbox.
2. Start `winter-vc-1` to restore the 30-step fine-tuned converter.
3. Remove `--no-voice-conversion` from the Web service and use a new metrics DB
   marker rather than mixing the rollback results with this path.

## Consequences

The currently unused Seed-VC service is stopped, returning roughly 2 GB of GPU
memory. This does not make Voice identity complete: question intonation,
spontaneous Korean fidelity and human naturalness still fail the final product
gate. Actual phone p50/p95 and ordinary daily listening decide whether this
limited path stays active.
