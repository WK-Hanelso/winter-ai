# Direct Memory Evidence and Temporal Safety

## 현재 가능한 형태

겨울이는 명시적 기억, 안정적 직접 진술, 검토된 현재 상태와 그 과거 이력을 SQLite에
보존한다. 같은 `current_state:<topic>`의 새 값은 이전 값을 deprecated로 만들지만 지우지
않아, 필요할 때 상태 변화를 비교할 수 있다.

별도의 synchronous direct-memory 경로는 문장 모양만으로 일부 1인칭 진술을 confidence 1.0
장기 semantic으로 즉시 활성화한다. 이 경로는 broad identity-shaped 문장에 대해 안정 사실과
일시 상태·맥락적 자기평가를 구분하지 못한다. 또한 새 Memory row에서 원문 conversation row와
시간 의미를 직접 추적할 수 없다.

## 이번 개발

Issue #116은 자동 기억의 범위를 줄이고 근거 계약을 강화한다.

```text
user conversation row
  → exact conversation reference
  → deterministic high-precision direct-memory gate
  → explicit temporal scope
  → candidate / active lifecycle
```

- conversation append가 정확한 원문 row reference를 Core에 돌려준다.
- 새 자동 Memory가 그 reference와 temporal scope를 보존한다.
- named fact와 명시적 안정 preference처럼 결정적으로 좁은 종류만 synchronous activation한다.
- 일시 상태나 broad self-appraisal은 영구 semantic으로 즉시 확정하지 않는다.
- 기존 reviewed current-state supersession/history는 보존한다.

## 결과

천우가 할 일은 계속 대화뿐이다. 겨울이는 안전하게 확정할 수 있는 정보는 자동으로 기억하되,
한 순간의 상태나 자기평가를 천우의 영구 정체성으로 굳히지 않는다. 개발·audit 시에는 새
자동 기억이 어느 원문에서 왔고 어떤 시간 의미로 저장됐는지 추적할 수 있다.

## 고도화 경계

- 이 작업은 local LLM Reflection의 recall을 높이지 않는다.
- 자동 current-state activation은 기존 품질 gate를 통과하기 전까지 켜지 않는다.
- 기존 Memory의 근거를 추측해 backfill하지 않는다. provenance가 없는 과거 row는 unknown으로
  정직하게 유지한다.
- embedding/vector 검색과 새로운 저장소 도입은 범위 밖이다.

## 2026-08-19 구현 검증

- SQLite와 in-memory Conversation Repository가 append한 정확한 row receipt를 반환한다.
- Core가 새 direct/explicit Memory에 `conversation:<message_id>`를 연결한다.
- synchronous direct activation은 named fact, 안정 preference와 명시적 decision으로 제한한다.
- broad identity-shaped state/self-appraisal fixture는 Memory를 만들지 않는다.
- 새 direct Memory는 `durable`, reviewed current state는 `episodic`, 근거가 없던 기존 row는
  `unknown` temporal scope를 사용한다.
- CLI와 Voice가 같은 Core에서 각자 실제 user row를 provenance로 기록한다.
- legacy DB migration fixture가 lifecycle·supersedes·history를 보존한다.
- 실제 `data/memories.sqlite`의 임시 복사본에서도 10개 row와 status/link 집계가 migration
  전후 동일했고 새 column만 추가됐다. 임시 복사본은 검증 직후 삭제했다.
- 집중 59 tests와 전체 offline 702 tests, Ruff 전체, mypy 161 source files를 통과했다.

원본 운영 DB에는 아직 migration을 적용하지 않았으므로 현재 maturity는 Shadow다. 구현
commit 뒤 CLI와 Web process를 새 코드로 기동하고 원본 row 보존을 재확인한 뒤 제한 운영으로
승격한다.
