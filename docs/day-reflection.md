# Winter Day Reflection V0

## Why this exists

Winter should feel like the same presence tomorrow without turning every conversation into a large
autonomous-learning pipeline. Day Reflection is the smallest bridge between two real Korean days.

```text
actual completed day
→ one bounded local-model reflection
→ evidence validation
→ local day_reflections.sqlite
→ next CLI / Web / Voice context
```

Cheonu only talks. At 04:10 Asia/Seoul, a user systemd timer looks for the oldest completed event day
that has not been closed. With no real Winter Event it does nothing. A failed local-model call leaves
the day unclosed and the oneshot service retries after 30 minutes.

## What one day may contain

At most one of each:

- `shared_moment`: something actually discussed together
- `cheonu_change`: a direct, provisional observation of a state change
- `open_thread`: something explicitly left unfinished
- `winter_reflection`: Winter's revisable thought about its response choice
- `next_cue`: one natural cue for a later conversation

Every point must cite conversation message ids from the same Korean day. A day also requires both
user and assistant messages plus at least one Winter Event. Old history from before Event recording
is not backfilled as lived experience.

## Safety and limits

- A current or future day cannot be closed.
- One date is immutable and idempotent after it is closed.
- Fake model output cannot enter the real store.
- The prompt forbids unseen activity, bodily experience, new Identity, capability and permission.
- The next-turn context labels every point as a fallible prior reflection. The current user statement
  always wins.
- Day Reflection does not activate Memory, Belief, Capability or Worker jobs.
- Exact evidence ids prove provenance, not semantic truth. Real-day human review remains required.

Developer inspection commands:

```bash
./winter days
./winter day-close --winter-day YYYY-MM-DD
systemctl --user status winter-day-close.timer
```

The first maturity gate is three real closed days with no invented event, user-state assertion,
stale continuation or noticeable degradation in the next conversation.
