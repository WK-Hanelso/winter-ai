# ADR-0027: exact lifecycle-to-source capability cycle

- 상태: Accepted, Shadow only
- 날짜: 2026-08-20
- Issue: #124

## 문제

#121의 Improvement lifecycle은 독립 검증 뒤 천우가 채택을 승인하면 즉시 `adopted`가 됐다.
#122와 #123에서 isolated ChangeProposal, source apply, full verifier와 rollback을 별도로
증명했지만 세 저장소는 연결되지 않았다. 따라서 장부는 채택됐다고 말하지만 source는 적용되지
않고 Capability Registry에도 능력이 없는 거짓 완료 상태가 가능했다.

## 결정

### 승인과 완료 분리

Improvement 상태를 다음처럼 분리한다.

```text
ready_to_adopt
  -> adoption_approved
  -> adopted

adoption_approved
  -> adoption_failed
  -> discussing (새 proposal 논의)
```

`adoption_approved`는 천우의 채택 의사만 뜻한다. 실제 source와 capability를 반영했다는 뜻이
아니다. `adopted`는 source proposal이 `applied`이고 exact Capability가 `available`인 것을
coordinator가 확인한 뒤에만 기록한다.

### 한 번의 사용자 승인과 exact binding

천우에게 ChangeProposal 채택을 다시 묻지 않는다. 한 번의 adoption approval record에 다음
identity를 함께 보존한다.

- Improvement ID, proposal version과 proposal SHA-256
- Worker result record SHA-256과 validation report SHA-256
- ChangeProposal ID와 patch SHA-256
- privacy-safe project baseline manifest SHA-256
- normalized Capability specification SHA-256

모든 hash 필드는 64자리 lowercase SHA-256 digest만 허용한다. coordinator가 이 binding을
다시 확인한 뒤 내부적으로 ChangeProposal의 source-adoption decision을 만든다.

### 적용 순서

```text
천우의 exact 채택 승인
  -> ChangeProposal adoption_approved
  -> #123 source apply + full repository verifier
  -> Capability proposed -> approved -> building -> testing
  -> health/test/adoption evidence
  -> Capability available
  -> Improvement adopted
```

Capability의 `adopted_change`에는 exact Improvement identity, `rollback_target`에는 exact
ChangeProposal/patch identity를 기록한다. 다른 definition으로 이미 존재하는 capability ID는
source write 전에 거부한다.

### 재시작과 실패

새 coordinator 전용 DB는 만들지 않는다. Improvement, ChangeProposal과 Capability Registry의
기존 persistent state가 journal 역할을 한다.

- source decision 전: 기존 approval에서 계속
- `adoption_approved`: source apply 실행
- `applying`: #123 recovery로 before/after/unknown 판정
- `applied`: Worker나 patch를 다시 실행하지 않고 Registry 단계 계속
- Capability `proposed`/`approved`/`building`/`testing`: 다음 전이부터 계속
- Capability `available`: Improvement completion만 기록
- Improvement `adopted`: 세 저장소가 일치할 때 idempotent success

검증 실패처럼 source가 before bytes로 안전하게 복원됐음을 확인한 경우 Capability를
`available`로 만들지 않고 Improvement를 `adoption_failed`로 기록한다. unknown bytes나 복원
실패처럼 안전을 증명할 수 없으면 자동 상태 완료를 하지 않고 수동 복구 상태를 유지한다.

### Rollback

exact adoption completion hash를 대상으로만 다음 순서를 실행한다.

```text
source rollback
  -> Capability retired
  -> Improvement rolled_back
```

각 단계 뒤 process가 종료돼도 이미 끝난 단계는 반복하지 않고 다음 단계부터 재개한다.

## 검증

temporary synthetic project에서 다음을 offline으로 검증한다.

- 한 번의 승인으로 source applied, Capability available, Improvement adopted 완주
- capability spec mismatch의 write 전 차단
- post-apply verifier 실패 시 before bytes 복원과 `adoption_failed`
- source applied와 Capability testing 중간 상태에서 재시작
- persistent/idempotent rollback
- Worker 호출 횟수 1회 유지

## 의도적으로 하지 않는 것

- 운영 Winter repository 변경
- 실제 Codex write Worker 또는 사용자 대화 route 연결
- synthetic Capability의 runtime 연결
- commit, push, deploy 또는 service restart
- 여러 SQLite를 하나의 원자적 transaction으로 결합
- hostile concurrent writer에 대한 OS lock
- operating verifier sandbox 활성화

## 결과와 다음 gate

겨울이의 “채택했다”는 기록이 처음으로 exact source와 Capability 상태 뒤에 오도록 순서를
증명했다. 다만 이것은 temporary Registry의 Shadow capability이며 실제 겨울이가 사용할 수
있는 능력이 아니다. 다음 단계는 operating verifier를 기존 격리 경계와 결합하고 공격 suite를
통과한 뒤, 천우가 고른 실제 작은 capability 하나를 사람 검토 아래 이 cycle로 연결하는 것이다.
