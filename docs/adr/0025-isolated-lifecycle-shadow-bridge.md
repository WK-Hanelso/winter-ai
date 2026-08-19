# ADR-0025: 승인 lifecycle과 격리 수정안의 Shadow-only 연결

- 상태: Accepted
- 날짜: 2026-08-19
- Issue: #122

## 문제

#121의 Improvement lifecycle과 기존 isolated implementation proposal은 각각 안전 경계를
가졌지만 서로 다른 상태 저장소에서 독립적으로 동작했다. 실제 연결 전에 다음 불일치를
해결해야 했다.

- 같은 구현 승인인지 추측하지 않고 증명할 identity가 없다.
- Worker artifact 생성과 host verification 결과가 한 상태처럼 보일 수 있다.
- 두 SQLite 기록 사이에서 process가 중단되면 Worker를 중복 실행할 수 있다.
- lifecycle의 채택 승인이 기존 source apply를 의도치 않게 호출할 수 있다.

## 결정

`IsolatedLifecycleBridge`를 Shadow 전용으로 추가한다. 현재 `ImprovementProposal`의 ID,
version과 hash로 deterministic `ImplementationAuthorization` ID를 만들고, solution,
acceptance criteria와 exact implementation paths만 격리 Worker에 전달한다.

```text
approved Improvement
  → deterministic authorization
  → isolated patch proposal 저장
  → lifecycle WorkerResult(artifact id + patch hash)
  → persisted host verification을 별도 판정
  → ready_to_adopt
```

격리 patch가 저장됐다는 사실은 Worker artifact 성공일 뿐이다. lifecycle은 이때
`validating`에 머문다. bridge가 저장된 `python_unittest`와 `python_structure` 결과의 status와
exit code를 다시 대조하고 exact patch hash가 같을 때만 validation report를 만든다.

Worker가 수정한 모든 path는 승인 proposal의 implementation scope 안에 있어야 한다. Worker
summary는 lifecycle 판단이나 천우에게 보이는 설명 근거로 사용하지 않는다.

## 재시작과 중복 실행

authorization ID는 deterministic이고 change proposal 저장소에서 unique하다.

- lifecycle이 `implementing`이고 같은 authorization의 격리 patch가 이미 있으면 Worker를
  다시 호출하지 않고 그 artifact identity를 복원한다.
- lifecycle이 `implementing`인데 격리 patch가 없으면 이전 Worker가 실행됐는지 알 수 없으므로
  자동 재실행하지 않는다. 명시적 복구가 필요하다는 오류로 fail-closed한다.
- 잘못된 proposal hash, 다른 authorization, scope 초과, patch identity 불일치는 다음 상태로
  진행하지 않는다.

## 의도적으로 하지 않는 것

- lifecycle adoption을 기존 `ChangeProposal`의 apply 승인으로 변환
- 실제 source tree patch 적용
- Capability Registry 등록 또는 `available` 전이
- 실제 Codex/Claude/GPT 호출
- 사용자-facing 자동 실행
- account, network 또는 외부 write 권한

## 결과와 다음 gate

승인된 문제와 격리된 기술 수정안이 동일한 작업이라는 것을 재시작 뒤에도 증명할 수 있다.
그러나 이번 단계의 `adopted`는 lifecycle 결정일 뿐 source나 capability가 바뀌었다는 뜻이
아니다. 다음 단계는 실제 apply 전에 project 전체 baseline, crash recovery, full repository
verification profile과 별도의 source-adoption 연결을 검증하는 것이다.
