# ADR 0033 — CharacterSnapshot과 ContextAssembler

- 상태: Accepted
- 날짜: 2026-09-10
- 범위: Epic #127 / T1.2 #130
- 선행: [ADR 0032 — Text runtime bootstrap](0032-text-runtime-bootstrap.md)

## 목적과 경계

기존 Core의 prompt 조립을 독립적으로 교체할 수 있게 한다. 모델·대화 정책·검색·DB schema를
동시에 바꾸지 않는다. 이 단계는 관계나 말투 품질 향상 실험이 아니라 기존 동작 보존이다.

`dialogue/character_snapshot.py`의 frozen `CharacterSnapshot`은 이미 선택된 Identity,
SelfModel, ImprovementSnapshot, 지난 회고, Belief와 Open Loop의 읽기 전용 값이다.
기존 version, source와 evidence를 그대로 보유한다. 새 관계 DB, 자동 정체성 갱신이나
권한 부여 기능은 없다. 여러 DB를 한 transaction으로 읽는 atomic snapshot은 아니다.

`dialogue/context_assembler.py`의 `ContextAssembler`는 snapshot, 현재 입력, 선택된 최근
대화, 기억/상태 이력, 과거 대화 발췌와 이미 결정된 지침을 `ChatRequest`로 조립한다.
모델 호출·저장소 조회·데이터 갱신·대화 행동 판단을 하지 않는다.

```text
기존 Core: 저장·검색·대화 정책 선택
  → CharacterSnapshot + 최근 대화/기억/지침
  → ContextAssembler: 순서대로 ChatRequest 조립
  → 기존 Local ChatModel → 기존 검토·응답 저장
```

## 보존한 계약

system message 순서는 다음과 같다. 빈 optional 블록은 기존과 동일하게 생략한다.

1. Identity
2. SelfModel
3. 개선 안건
4. 지난 회고
5. 선택된 Memory
6. 현재 상태 이력
7. Winter Belief
8. 검색된 과거 대화
9. Open Loop
10. 해당 turn의 말투 지침
11. grounding과 대화 행동을 합친 최종 지침

이후 기존 최근 대화가 붙는다. explain 224 / 나머지 120 max_tokens와 최종 지침이 비었을
때도 system 블록을 유지하는 동작을 보존했다. 말투는 Core에서 한 번만 결정한다.
repair는 같은 request에 초안/수정 지침만 추가하며 상태를 재조회하거나 말투를 다시 뽑지 않는다.

Core 생성자 끝에 선택적 `context_assembler` 주입점을 추가해 기존 호출을 유지한다.
기존 `context.py`의 최근 대화 budget, 전체 history 조회, 검색 및 maintenance queue의 등록
위치는 바꾸지 않았다. CLI/Web/Voice는 같은 Core 경로를 사용하고 public import는 유지한다.
읽기/조립/모델 오류는 fake나 빈 snapshot으로 대체하지 않는다.

## 검증 기준과 한계

수정 전 `393bc4c`에서 고정한 합성 입력 6종의 ChatRequest 전체 직렬화 SHA-256을
`tests/fixtures/text-context-393bc4c.json`에 보관했다. 최소 문맥, bounded recent, 모든 선택
블록, explain, streaming, repair를 포함한다. style RNG도 고정해 비결정적 차이를 제거했다.
수정 뒤 이 지문은 재생성하지 않는다. 향후 의도적으로 정책을 변경하면 baseline을 덮어쓰지
말고 새 정책의 평가와 구분한다. system 11개 블록 순서는 별도의 내용 assertion으로 확인한다.

스냅샷 불변성·기존 근거 보존·다음 turn의 상태 반영·repair 재사용·실패 전파를 검사한다.
이전 bootstrap의 임시 SQLite 재시작 테스트와 Shared Core 회귀도 유지한다.
전체 실행 결과는 [TODO T1.2](../text-first-implementation-todo.md)에 기록한다.

실제 모델의 새 응답 품질이나 사용자의 체감은 미검증이다. 전체 history 조회 제거, 전체
token 예산과 새 EvidenceBundle은 T3, 관계/자연스러움 정책 비교는 T2다. 운영 DB·모델·서비스,
Voice/Reference를 변경하지 않았다. schema가 그대로이므로 코드 rollback에 migration은 없다.
