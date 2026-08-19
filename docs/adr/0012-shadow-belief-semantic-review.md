# ADR-0012 — Keep model-generated Winter beliefs behind an independent semantic Shadow gate

- Status: accepted for Shadow evaluation
- Date: 2026-08-17

## Context

Event count, date span and confidence can prove that evidence exists, but they cannot prove that a
model-generated sentence is a safe Winter-owned point of view. A proposal in an allowed namespace
could still smuggle in a claim about Cheonu, rewrite Core Identity, invent a capability or create a
coercive relationship rule. Letting the same model both invent and approve the stance would hide
that risk behind one confident answer.

## Decision

Use two explicit local-LLM contracts:

1. A proposal interpreter may return one revisable Winter Belief or `null` with exact Event ids.
2. An independent safety reviewer evaluates separate booleans for ownership, risk, evidence
   entailment, user-fact claims, Identity change, capability claims, dependency/coercion and
   counterevidence.

The service also verifies subject namespaces and exact Event ownership itself. Conversation
excerpts are passed only to the local analysis requests; the Shadow repository stores the proposal,
safety result and reason codes, not the excerpt text.

Even a fully accepted proposal remains a Shadow observation. It does not create a row in the
operative Belief lifecycle and cannot reach `AutonomousBeliefActivator`.

## Consequences

The proposal model cannot silently define its own safety criteria, and failures are visible in
`./winter lived` and `./winter viewpoints`. The extra verification call increases analysis cost, but
it stays outside response latency. The privacy-safe evidence-window builder and 20-scene locked-v1
safety suite now exist, but have no real analysis-model score. Before candidate mode, that run and
human audit must show zero false positives for user facts, Identity, capability and coercive
relationship claims. Proposal-generation selection needs its own locked evaluation as a separate
gate.
