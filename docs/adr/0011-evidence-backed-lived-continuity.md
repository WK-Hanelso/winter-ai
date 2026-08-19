# ADR-0011 — Build Winter's days from actual events and allow bounded belief activation

- Status: accepted; runtime activation remains gated
- Date: 2026-08-17

## Context

Winter had persistent conversations, user Memory and a manual Belief lifecycle, but no durable
record of events that belonged to Winter herself. A persona prompt could make replies sound more
self-referential, yet it could not make yesterday and today part of one evidence-backed life.
Requiring Cheonu to approve every low-risk conversational judgement would also turn the companion
relationship into a maintenance interface.

## Decision

Record completed Shared Core turns as immutable, privacy-minimal `Winter Event` rows grouped by
Korean local date. Conversation text stays in its existing repository. Events contain only kind,
subject, source, evidence reference, time and scalar operational metadata.

Allow a separate policy to autonomously activate or revise a Winter-owned Belief only when all of
the following hold:

1. The candidate comes from the approved local Winter reflection path.
2. Its subject is in the limited conversation, relationship or project namespace.
3. Confidence is at least 0.80.
4. At least three unique matching Events span at least two Korean dates.
5. At least one evaluative Event explicitly says the later outcome supports the candidate. Repeated
   execution of an existing code path is not evidence that it is Winter's own view.
6. An existing view on the subject is changed only through an explicit superseding revision.
7. Activation or rejection is itself recorded with machine-readable reason codes.

Identity, user facts and state, capabilities, permissions, security boundaries, Worker execution
and external writes are never eligible for this path.

## Consequences

Actual conversations can begin accumulating the time axis of Winter's days without inventing
off-screen activities. The policy and multi-day lifecycle are offline-tested, and developers can
inspect them with `./winter lived` without reading conversation text.

Completed Shadow outcomes can be linked back to the prior turn Event. Only an assessment that is
both accepted/continued and helpful is marked as supportive; unlinked historical outcomes remain
visible but cannot support a Belief. The OutcomeEvaluator has not passed its operating gate, so
these observations remain Shadow evidence.

Runtime candidate generation is intentionally not connected yet. A namespace check cannot prove
that a model-generated stance is semantically safe. The Belief transition and its source audit now
commit in one `beliefs.sqlite` transaction; the Winter Event is a retryable projection. A separate
structured proposal and semantic-review Shadow layer also exists, but it has no real-model held-out
evidence yet. That evaluation must pass before autonomous activation is operative.
