# ADR-0014 — Explicit Read-only Codex Worker First

- 상태: Accepted
- 날짜: 2026-08-17

## Context

겨울이의 관계, Memory와 Voice 기반은 실제로 동작하지만 전문 코드·프로젝트 작업 능력은
작은 Local Conversation LLM에 묶여 있다. 천우는 겨울이가 정체성과 최종 대화를 유지하면서
Codex나 Claude 수준의 Worker를 실제로 사용해야 한다고 결정했다. 동시에 전체 대화,
Memory, Identity, 음성과 account credential을 외부 모델에 넘겨서는 안 된다.

## Decision

첫 Worker capability는 OpenAI Codex CLI의 read-only 분석으로 제한한다.

- 같은 사용자 발화에 Codex 이름과 작업 동사가 모두 있을 때만 turn별 opt-in으로 route한다.
- 허용된 project text를 임시 bundle로 복사하고 private/runtime 경로와 binary를 제외한다.
- Codex는 `ephemeral`, `read-only`, 호환 모델 `gpt-5.4`와 strict result schema로 실행한다.
- 결과는 answer, evidence, limitations로 검증하며 Winter가 Worker 사용 사실을 밝히고 전달한다.
- 실패 시 Local LLM으로 silent fallback하지 않는다.
- CLI·Web·Voice가 같은 `CompanionCore` route와 Conversation/Event 기록을 공유한다.
- 이 결정은 코드 write, 구현 승인, Capability 채택이나 account write 권한을 허가하지 않는다.

## Consequences

겨울이는 실제 전문 분석을 수행할 첫 경로를 얻는다. 일반 관계 대화는 계속 local-first이고,
외부 전송은 명시한 턴으로 한정된다. 폰에서는 Worker 대기 상태가 보이고 결과를 동일한 Voice로
듣는다.

반면 sanitized bundle만으로 모든 문제를 분석할 수 없으며, Codex CLI의 host read 범위는
아직 별도 OS container로 강제하지 않았다. 한 번의 실제 성공은 장시간 reliability나 비용을
입증하지 않는다. 다음 단계는 20개 실제 read-only task audit이고, write 경로는 승인 state
machine과 격리 worktree가 생긴 뒤 별도 ADR로 결정한다.
