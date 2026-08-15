# ADR-0005: 겨울이의 관점을 Persona·Memory와 분리해 영속화한다

- 상태: Accepted
- 날짜: 2026-08-15

## 문제

겨울이가 자기 생각을 말하게 한다는 요구를 prompt 문구나 상황별 고정 대사로 구현하면
그것은 개발자가 써 둔 의견이다. 반대로 Local LLM이 매 턴 자유롭게 입장을 만들게 두면
같은 주제에서 어제와 오늘의 판단이 모순되어 지속적인 주체로 느껴지지 않는다.

기존 저장소의 두 상태도 이 문제를 대신할 수 없다.

- `CompanionIdentity`는 사용자의 승인 없이 바뀌지 않는 Core Persona와 가치다.
- `Memory`는 천우에 대한 사실·선호·결정이며 명시적 승인 lifecycle을 따른다.

겨울이가 실제 관찰을 바탕으로 형성하고 새 근거에 따라 수정할 수 있는 판단은 둘 중
어느 것도 아니다.

## 결정

`Belief`를 별도 domain concept와 local SQLite 저장소로 둔다.

```text
subject
stance
rationale
confidence
evidence[]
status
source
created_at / updated_at
supersedes
```

Belief는 다음 규칙을 지킨다.

1. 근거가 하나도 없는 후보는 저장하지 않는다.
2. 생성된 후보는 바로 대화에 쓰지 않고 `candidate`로 둔다.
3. 검토된 `active` Belief만 현재 질문과 관련 있을 때 Core context에 들어간다.
4. 같은 `subject`에 두 개의 active Belief가 조용히 공존할 수 없다.
5. 수정은 기존 행을 덮어쓰지 않는다. 새 candidate가 `supersedes`를 가리키며, 새
   관점이 active가 되는 순간에만 이전 관점이 deprecated가 된다.
6. context에서는 Belief를 고정된 사실이 아닌 수정 가능한 판단이라고 명시한다.

당시 결정한 system context 순서는 다음과 같았다.

```text
Identity → User Memory → Winter Belief → Verbal Style → Grounding → Conversation
```

Grounding은 여전히 생성 턴에 가장 가까운 제약이다. 관점이 있다는 이유로 근거 없는
사실까지 만들어서는 안 된다.

Open Loop·old conversation recall·DialogueDirector가 추가된 뒤의 현재 순서는
[ADR-0006](0006-shared-continuity-and-dialogue-control.md)이 대체한다.

## 이번 변경의 범위

- evidence-backed Belief lifecycle과 충돌 방지
- 관련 active Belief 검색
- `CompanionCore`, text CLI, Voice CLI, phone web 경로에 동일한 context 연결
- CLI를 통한 후보 등록·검토·수정

자동 Reflection은 포함하지 않는다. 저장·수정·철회가 검증되기 전에 LLM에게 자동 쓰기
권한을 주면 즉흥 출력을 영속적인 관점으로 오염시킬 수 있기 때문이다.

## 다음 결정

`DialogueDirector`와 열린 이야기 상태는 [ADR-0006](0006-shared-continuity-and-dialogue-control.md)에서
구현했다. 다음 단계에서는
Reflection이 대화·실험·Worker 결과에서 Belief candidate를 제안하되, Worker가 저장소를
직접 수정하지 못하도록 한다. 자동 활성화 조건은 별도 평가 전에는 추가하지 않는다.

## 결과

- 의견을 코드에 하드코딩하지 않고도 여러 턴에 걸쳐 일관된 관점을 사용할 수 있다.
- 판단의 근거와 변경 이력을 조회할 수 있다.
- Core Persona와 천우에 대한 Memory가 겨울이의 가변적인 판단에 의해 오염되지 않는다.
- 이 구조가 인간과 같은 주관적 의식을 의미하지는 않는다. 구현되는 것은 근거를 가진
  지속적이고 수정 가능한 관점이다.
