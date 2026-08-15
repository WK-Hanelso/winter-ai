# ADR-0007: Current State Snapshot and Timeline

- 상태: Accepted
- 날짜: 2026-08-15

## 문제

천우의 멘탈, 건강, 업무 부담 같은 현재 상태를 안정적인 성격이나 영구 사실로 저장하면
시간이 지나 상태가 바뀌어도 오래된 값을 현재처럼 사용할 수 있다. 반대로 새 값이 생길
때 이전 값을 삭제하면 겨울이는 천우의 변화와 과거 맥락을 이해할 수 없다.

## 결정

현재 상태는 `current_state:<topic>` Memory로 저장한다.

- 같은 topic에는 active 최신 값 하나만 둔다.
- 새 값은 이전 active 값을 `supersedes`하고 이전 값은 `deprecated`로 보존한다.
- `created_at`과 교체 시점의 `updated_at`으로 유효 구간을 표현한다.
- 평범한 대화에는 active snapshot만 전달한다.
- 상태 변화 질문에만 active/deprecated Timeline을 함께 전달한다.
- Timeline에는 인과 추측 금지 지시를 포함한다.
- CLI의 `./winter state`에서 모델 없이 전체 이력을 확인할 수 있다.

## 결과와 한계

최신 상태와 과거 상태를 모두 보존하면서 일상 응답에 오래된 상태가 섞이는 것을 막는다.
현재 자연어에서 새 상태와 topic을 자동 추출하는 기능은 아직 연결하지 않는다. 검증된
추출·사용자 확인 경로가 추가될 때 이 Timeline의 update API를 사용한다.
