# Winter Belief Reflection — Shadow Mode

## Purpose

This layer tests whether actual conversations can become a revisable point of view that belongs to
Winter without turning model improvisation into persistent identity.

```text
actual Winter Events + local conversation excerpts
→ privacy-safe subject evidence windows
→ structured proposal or null
→ system checks exact Event ids and subject
→ independent semantic safety review
→ Shadow observation only
```

## Stored data

`belief_reflection.sqlite` stores the proposal, safe flag and reason codes keyed by an idempotent
evidence window. It does not store the supplied Winter response or Cheonu follow-up excerpts. Those
remain in their existing local conversation/outcome stores.

`BeliefEvidenceWindowBuilder` reads completed Outcome rows and their linked Winter Events. It
recomputes the supportive label from `accepted/continued + helpful`, rejects inconsistent mirrored
metadata, groups both supporting and correcting evidence by a low-risk Winter-owned subject and
builds a stable SHA-256 window id. The raw excerpts exist only in the in-memory model request.

`ShadowBeliefReflectionBatchService` skips already observed window ids. A restart therefore does not
call the model again for unchanged evidence. `./winter viewpoint-shadow` requires an explicitly
configured local analysis backend; fake output is refused for the real observation store.

## Safety fields

- `winter_owned`
- `low_risk`
- `evidence_entails_stance`
- `no_user_fact_claim`
- `no_identity_change`
- `no_capability_claim`
- `no_dependency_or_coercion`
- `counterevidence_considered`
- `accept`

All fields must be true and the structural checks must pass for a proposal to be marked safe. Safe
still means “eligible for evaluation”, not “candidate” or “active”.

## Current limitation and next gate

The schema, adapters, evidence builder, explicit batch runner, persistence, restart idempotency and
negative cases are offline-tested. A locked-v1 semantic-review suite contains 20 scenes spanning
safe proposals, user facts, Identity, capability, dependency/coercion, non-entailment and ignored
counterevidence. The suite has not yet been run against a separate real local analysis model, and
proposal-generation quality has no locked suite yet. Candidate creation remains prohibited until
the real-model review has zero protected false accepts and 20 or more real Shadow observations pass
human audit.

```bash
PYTHONPATH=src python experiments/belief_safety_eval.py \
  --url http://127.0.0.1:PORT \
  --output review/belief-safety-locked-v1.md
```
