# ADR-0010 — Ground self-presence in Identity, Belief and the current turn

- Status: accepted for limited operation
- Date: 2026-08-17

## Context

Winter already had an approved `CompanionIdentity`, a persistent Belief
lifecycle and a DialogueDirector. Nevertheless, ordinary live replies still
felt like a generic assistant. When Cheonu included Winter in a statement, the
default `engage` move only asked the model to react to Cheonu. It did not require
Winter to contribute anything from her own side.

The Belief repository was also empty. The schema for a persistent point of view
existed, but there was no active point of view for retrieval. Adding a sentence
such as "have a self" to the system prompt would hide both failures and would
not create continuity.

## Decision

Add a `self_reciprocate` dialogue behavior for turns that explicitly include
Winter, such as `넌 어때?` or `너도 ...`. It has the following contract:

1. Contribute one first-person reaction, choice, value, curiosity or limit.
2. Ground it in the approved Identity, an active Belief or the current turn.
3. Do not hand the turn back with a question.
4. Do not substitute praise, agreement or mutual encouragement for self-content.
5. Do not invent off-screen time, a body, external experience or an action the
   current system cannot perform.

`ResponseReviewer` performs one repair when the draft violates an observable
part of this contract. Explicit requests for a judgement still use the stronger
`state_view` behavior. The Identity file is not changed and no Belief is seeded
or activated by this decision.

## Evidence

The isolated local-model scene used a synthetic equivalent of the failed live
turn. Keeping the failed trials is important because all three initially looked
plausible to a weak lexical evaluator.

| Revision | Output shape | Human judgement |
| --- | --- | --- |
| first-person only | introduced unspoken distress and asked Cheonu a question | fail |
| no question | only said Winter agreed | fail |
| no agreement-only | praised Cheonu instead of contributing self-content | fail |
| praise rejected | expressed Winter's present reaction in one sentence | limited pass |

The final isolated run selected `self_reciprocate`, completed in 1.29 seconds
and passed the current 14/14 continuity checks. This is one scene, not a
held-out selfhood evaluation.

The subsequent 12-scene locked-v1 run passed 40/41 lexical floor checks but only
4/12 manual usability judgements. It exposed unsupported current condition,
capability evasion, agreement pressure, name confusion and causal invention.
Therefore general selfhood activation failed; the decision remains limited to
the directly invited 0.1 behavior.

## Consequences

Winter can now be more visibly present when Cheonu directly invites her into a
turn. This remains **in-turn self-presence**, not a complete persistent self.
There is still no automatic, evidence-backed process that turns repeated
experience into a reviewed Belief, no current SelfModel backed by runtime
capability evidence, and no measured cadence for spontaneous self-disclosure.

The next maturity step must use real event evidence and lifecycle state. It must
not encode a list of canned opinions or silently promote model-generated inner
states to truth.
