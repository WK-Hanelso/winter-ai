# ADR-0021: Dedicated CPU Runtime for Shadow Maintenance

## Status

Accepted for Shadow operation on 2026-08-19. The 24-hour observation gate is pending.

## Context

TurnUnderstanding is queued automatically, but sharing the conversation endpoint would make
background analysis contend with real dialogue. A second GPU-resident model is unsafe on the
current 6 GiB GPU, and earlier Orin analysis load was not operationally stable.

## Decision

Run the existing Qwen3-4B Q4 checkpoint in a separately built CPU-only llama.cpp server bound to
`127.0.0.1:18081`. Supervise the model and worker as user services. Limit the runtime to four CPU
threads and one request, lower CPU/IO priority, process one event per cycle, and pause five seconds
after completed work. Give shutdown 130 seconds so the 120-second model request can reach a cycle
boundary. Keep prompts out of logs and record only aggregate queue and latency fields.

The worker consumes TurnUnderstanding only. Its outputs remain Shadow and cannot alter responses,
Memory, Identity, Belief, capability state, or Worker authority.

A transient timeout keeps its row pending, increments persistent `defer_count`, and rotates the row
behind work with fewer deferrals. This avoids both data loss and head-of-line starvation. A deferred
row is not relabeled as a schema failure, and no validator or quality gate is weakened.

## Rejected Alternatives

- Reusing `:18080`: couples maintenance to the conversation critical path.
- A second GPU model: exceeds the safe headroom observed on the current GPU.
- Running only by manual command: violates the zero-maintenance user contract.
- Activating extracted memories while draining: the interpreter quality gate is still failed.

## Consequences

Backlog processing is intentionally slow, around 42–60 seconds per observed event before the
inter-cycle pause. This is acceptable because it is off the response path. Service restart,
24-hour stability, real dialogue latency and human result quality remain explicit gates; none are
implied by successful canaries.
