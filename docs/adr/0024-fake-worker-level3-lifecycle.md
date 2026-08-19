# ADR-0024: 실제 실행 전 Fake Worker로 증명하는 Level 3 생명주기

- 상태: Accepted
- 날짜: 2026-08-19
- Issue: #121

## 문제

Improvement Ledger는 천우가 승인한 정확한 proposal version/hash를 보존하지만, 승인 이후를
곧바로 실제 Worker와 source 변경에 연결하면 다음 경계가 코드가 아니라 기대에만 남는다.

- 구현 승인과 채택 승인은 서로 다른 결정인가?
- Worker의 성공 보고를 독립 검증 없이 믿지 않는가?
- 재시작 뒤에도 실행·검증·채택의 정확한 근거를 복원하는가?
- 검증 실패, 순서 생략, 중복 실행과 성급한 rollback을 차단하는가?

## 결정

실제 Worker 대신 deterministic Fake Worker와 별도 Fake Verifier로 다음 상태를 먼저
offline에서 증명한다.

```text
approved
  → implementing
  → validating
  → ready_to_adopt
  → adopted
  → rolled_back

implementing ── Worker 실패 ──→ validation_failed
validating ── 검증 실패 ──────→ validation_failed → discussing
```

모든 cycle action은 현재 proposal version과 SHA-256 hash가 정확히 일치해야 한다.
`implementation_started`, `worker_result`, `validation_report`, `adoption_approval`, `rollback`
record는 같은 Improvement SQLite에 immutable payload와 record hash로 남긴다.

Worker 성공은 `validating`까지만 이동시킨다. 별도 Verifier가 Worker result hash와 acceptance
checks를 대상으로 통과해야 `ready_to_adopt`가 된다. 그 뒤 천우의 별도 adoption approval이
정확한 validation report hash를 지정해야 `adopted`가 된다. 구현 승인 decision은 채택에
재사용하지 않는다.

## 안전 경계

- 승인 전 또는 proposal version/hash 불일치 시 Worker 호출은 0회다.
- Worker와 Verifier는 별도 Port이며 Worker의 `succeeded` 값을 검증 결과로 사용하지 않는다.
- 실패한 검증, 누락된 검증, 잘못된 report hash는 채택할 수 없다.
- rollback은 실제 adoption record가 있을 때만 가능하고 그 근거와 이유를 보존한다.
- restart는 상태를 추측하지 않고 SQLite의 record와 transition history를 다시 읽는다.

## 의도적으로 하지 않는 것

- 실제 Codex/Claude/GPT Worker 호출
- repository source, branch 또는 worktree 수정
- Capability Registry를 `available`로 전환
- 계정 credential, 외부 network/write 권한 연결
- TurnUnderstanding signal의 자동 Improvement 등록
- 사용자-facing 대화에서 자동 구현·채택

## 결과와 다음 gate

이번 결정은 Level 3의 승인 경계를 실행 가능한 계약으로 만들지만 실제 능력을 추가하지
않는다. 다음 단계에서 기존 isolated implementation Shadow를 연결하더라도 같은 상태 전이를
통과해야 한다. 실제 capability 등록은 project 전체 baseline, crash recovery, 독립 검증과
천우의 채택 승인 뒤 별도 Issue에서 연결한다.
