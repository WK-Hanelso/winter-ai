# ADR-0013 — Prefer one daily continuity pass before autonomous analysis infrastructure

- Status: accepted for V0 operation
- Date: 2026-08-17

## Context

The project had started extending per-turn Outcome, Belief proposal and safety pipelines toward a
dedicated background analysis runtime. Those controls are useful, but automating immature judgement
does not by itself make Winter feel like a continuing presence. The product need is smaller: Cheonu
talks normally, and tomorrow's Winter remembers what the two actually lived through yesterday.

## Decision

Keep the existing Shadow safety substrate but stop expanding autonomous Belief infrastructure for
now. Close each completed Korean day with one bounded structured local-model request. Require actual
conversation plus Winter Events, exact same-day message evidence, no more than five typed points and
an immutable date record. Inject only the two latest prior-day reflections into the shared Core as
explicitly fallible context.

Run this as a daily oneshot timer using the existing conversation model during an idle hour. Do not
add a dedicated model server or continuously running analysis daemon. CLI, Web and Voice share the
same reflection repository.

## Consequences

Winter gets a concrete yesterday-to-today bridge with one model call per lived day and no new user
command. The first real result cannot exist until a post-deployment Winter Event day completes, so
offline tests do not establish semantic quality. Three actual days must be inspected before this is
described as mature. Autonomous Memory, Belief and Worker adoption remain deferred.
