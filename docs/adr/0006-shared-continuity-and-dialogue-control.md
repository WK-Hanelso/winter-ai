# ADR-0006: 모든 인터페이스가 관계 상태를 공유하고 대화 행동을 검증한다

- 상태: Accepted
- 날짜: 2026-08-15

## 문제

CLI, phone web, Voice가 같은 `CompanionCore` 타입을 사용했지만 실제 상태는 같지 않았다.

- CLI는 `data/conversations.sqlite`를 사용했다.
- web은 `data/conversation.sqlite`를 사용했다.
- Voice CLI는 대화를 메모리에만 두어 종료하면 잃었다.
- web과 Voice에는 explicit Memory 조회·저장이 연결되지 않았다.

대화 생성도 Identity와 말투 지시만으로는 충분하지 않았다. 실제 Orin 모델은 힘든 말에
`어떤 부분이 힘들어?`를 되묻고, 의견 비교에는 `상황에 따라 다르다`고 회피했다. 이는
문장을 더 자연스럽게 쓰는 문제가 아니라, 이번 turn에 할 행동을 정하고 실패를 검증하는
계층이 없다는 문제였다.

## 결정

### 하나의 영속 상태

모든 사용자 인터페이스의 기본 경로를 다음으로 통일한다.

```text
data/conversations.sqlite   exact conversation log
data/memories.sqlite        facts/preferences explicitly remembered for 천우
data/beliefs.sqlite         reviewed, revisable Winter judgements
data/dialogue_state.sqlite  unfinished conversational threads
```

기존 web DB 98개와 CLI DB 24개 메시지는 exact `role/content/created_at` 기준으로 중복을
제거하고 시간순으로 합쳐 122개가 됐다. 기존 `data/conversation.sqlite`는 삭제하지 않아
원본 및 rollback 근거로 남긴다.

### 기억의 네 경로

1. 최근 12개 메시지는 bounded conversation context로 전달한다.
2. 더 오래된 대화는 현재 문장과 lexical relevance가 있을 때 정확한 turn 원문만 최대
   두 개 검색한다. 이를 새로운 사실로 요약하지 않는다.
3. `기억해 …`는 사용자의 명시적 저장·승인 명령이므로 repository 내부에서
   `candidate → approved → active` 전이를 기록하고 즉시 사용할 수 있게 한다.
4. 안정적인 1인칭 직접 진술은 명령이 없어도 사용자 원문 그대로 같은 lifecycle을 거쳐
   active로 만든다. 질문, 순간 상태, 불확실 표현과 제3자 진술은 제외한다. 같은 named
   fact 또는 같은 대상에 대한 반대 선호는 새 항목이 기존 항목을 supersede한다.

4번은 regex가 확인한 직접 진술이지 모델의 요약이나 해석이 아니다. 자동 추론한 사실은
이 경로를 사용할 수 없다. 향후 Reflection 결과는 계속 candidate로 남겨야 한다.

### Open Loop

`내일 다시 보자`, `아직 고민 중이야`처럼 사용자가 직접 미완성을 표시한 발화만 verbatim
Open Loop로 저장한다. `아까 얘기 이어가자` 같은 모호한 후속 발화에는 최근 open thread를
제공한다. 모델이 다른 최근 화제를 선택하면 한 번 재생성하고, 그래도 실패할 때만 저장된
원문에서 만든 짧은 topic label로 연결한다. 이 fallback은 의견을 만들지 않고 무엇을
이어갈지만 보존한다.

### DialogueDirector와 ResponseReviewer

`DialogueDirector`는 내용을 하드코딩하지 않고 이번 응답 행동만 선택한다.

```text
listen / state_view / continue_thread / defer_thread
explain / answer_then_reciprocate / engage / acknowledge_memory
```

`ResponseReviewer`는 실제로 반복 관찰된 실패만 검사한다.

- 힘든 말에 넓은 질문으로 되돌리기
- 비교 의견에서 선택을 회피하기
- 미뤄 둔 화제를 같은 turn에 다시 열기
- Open Loop 대신 다른 최근 화제를 선택하기
- 상투적인 도움 제안으로 끝내기
- `A보다 B` 형태의 활성 기억을 회상하면서 비교 한쪽을 생략하기

실패한 초안만 한 번 재생성한다. 두 번째도 Open Loop focus를 놓치면 factual continuity
fallback을 사용한다. 비교형 활성 기억도 두 번째 응답이 양쪽 핵심을 보존하지 못하면
저장된 사용자 원문을 명시적으로 인용한다. Winter의 의견 자체는 fallback으로 고르지
않는다.

일반 대화는 최대 2문장·120 output token, 명시적 설명은 최대 4문장·224 token으로
제한한다. text와 Voice가 다른 답을 갖지 않도록 같은 bounded lexical response를 저장하고
말한다. 검증을 위해 streaming Voice는 첫 문장을 즉시 합성하기 전에 짧은 전체 응답을
buffer한다. Orin의 실제 전체 생성이 약 1~2초이므로 이 품질 trade-off를 수용한다.

## Prompt 순서

```text
Identity
→ active User Memory
→ active Winter Belief
→ related old conversation excerpt
→ related Open Loop
→ Verbal Style
→ Grounding + selected Dialogue behavior
→ recent conversation
```

Grounding과 turn behavior는 작은 모델의 recency에서 서로 밀어내지 않도록 마지막 system
block 하나에 함께 둔다.

## 검증

실제 Orin model을 세 번의 독립 `CompanionCore` 인스턴스로 실행해 다음을 검증한다.

- 힘든 말에 broad question을 돌려주지 않음
- 비교 질문에서 하나의 현재 판단을 말함
- 프로세스 재시작 뒤 Open Loop 복귀
- `기억해` 즉시 활성화와 재시작 뒤 자연어 회상
- `기억해` 없는 직접 선호 진술의 자동 활성화와 재시작 뒤 자연어 회상
- 일반 turn 2문장 경계

재현 명령과 최신 결과는 `experiments/companion_dialogue_eval.py`와
`review/companion-cli-eval/nightly-2026-08-15.md`에 남긴다.

## 남은 한계

- Open Loop detection은 명시적 한국어 신호에 한정된다.
- 오래된 대화 retrieval은 lexical이며 embedding semantic search가 아니다.
- 반복 관찰로 Memory/Belief candidate를 제안하는 Reflection은 아직 없다.
- 관계 친밀도나 장기 mood state는 아직 별도 domain state가 아니다.
- 한 번의 repair는 출력 형태를 안정화하지만 작은 모델의 깊은 상황 이해를 보장하지 않는다.
