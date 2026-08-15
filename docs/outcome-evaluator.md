# OutcomeEvaluator Shadow Mode

## 목적

`OutcomeEvaluator`는 겨울이가 이전 사용자 의도를 어떻게 해석했고 실제로 무엇을 답했는지,
그 다음 천우 발화가 확인·정정·거절·계속·중단 중 무엇을 나타내는지 연결한다.

현재는 **Shadow Mode**다. Outcome은 Memory confidence, DialogueDirector, 다음 답변과 model
weight를 변경하지 않는다.

```text
Turn N user → TurnUnderstanding ID → Winter actual response
                                      │
Turn N+1 user ────────────────────────┘
                 │
                 ▼
        pending Outcome event
                 │ separate CLI analysis
                 ▼
       completed / failed / deferred
```

## 저장과 lifecycle

CLI, phone Web과 Voice는 `data/outcomes.sqlite`를 공유한다. 각 턴은 대응하는
`TurnUnderstanding` ID, source, 사용자 원문과 실제 최종 응답을 저장한다. 응답이 정상
완료된 턴만 다음 사용자 턴과 연결한다. schema v2부터 Core가 그 답변에 실제로 주입한
active Memory의 ID·kind·content도 함께 보존한다. schema v3는 모델 평가와 분리된
천우의 blind review를 추가하며 기존 turn과 assessment를 삭제하거나 다시 평가하지 않는다.

- `pending`: 이전 응답과 다음 사용자 턴이 연결됐지만 아직 평가하지 않음
- `deferred`: DB 상태가 아니라 processing 결과다. 이전 TurnUnderstanding이 아직
  `pending`이므로 Outcome event를 그대로 보존함
- `completed`: local evaluator 결과가 schema와 exact-evidence 검증을 통과함
- `failed`: 이전 TurnUnderstanding 실패, local model 실패 또는 결과 검증 실패

한 이전 턴에는 바로 다음 턴 하나만 연결한다. CLI에서 대화한 뒤 Web이나 Voice로 이어도
같은 사용자의 연속 턴으로 처리한다.

## Outcome contract

```text
outcome                    accepted / corrected / rejected /
                           continued / abandoned / unclear
confidence                 0.0 ~ 1.0
evidence                   다음 사용자 원문의 exact substring
intent_match               confirmed / contradicted / partial / unclear
response_usefulness        helpful / unhelpful / mixed / unclear
affect_shift               improved / worsened / unchanged / unclear
memory_confirmation        명시적 확인만
memory_contradiction       명시적 반박만
improvement_confirmation   겨울이 실패·개선 필요의 명시적 확인만
uncertainties              단정하지 못한 부분
```

침묵, 단순 주제 전환과 다음 대화의 존재 자체를 acceptance나 helpful로 간주하지 않는다.
memory confirmation은 모델 초안이 아니라 다음 사용자 evidence가 있을 때만 제안할 수 있다.
Memory relation evaluator는 Core가 실제 사용한 Memory ID만 받을 수 있으며 provenance가
없으면 호출하지 않는다. Improvement candidate는 별도 모델이 만들지 않고 negative
categorical outcome과 직접 failure field의 일관된 조합에서 파생한다.

## 사용법

일반 대화는 Outcome event만 빠르게 남긴다. 먼저 이전 TurnUnderstanding을 처리한 뒤
Outcome을 평가한다.

```bash
./winter chat --analyze-pending-turns --analysis-limit 10
./winter chat --analyze-pending-outcomes --analysis-limit 10
./winter chat --list-outcomes
./winter chat --review-outcomes
./winter chat --outcome-audit
```

`--review-outcomes`는 모델 label을 숨긴 채 `이전 질문 → 실제 답변 → 다음 반응`을 한 묶음씩
보여준다. 천우가 숫자로 outcome·의도 이해·도움 여부·감정 변화·사용된 기억·개선 필요를
고른 뒤에만 모델과의 일치 여부가 보인다. `s`로 넘기거나 `q`로 종료할 수 있고 기본 한 번에
5개만 보여준다. `--review-limit 10`으로 바꿀 수 있다.

`--outcome-audit`는 30개 gate까지의 진행률, 주요 네 필드의 일치율, Memory와 Improvement의
잘못 감지와 놓침을 보여준다. 두 명 이상이 독립 검토할 때만 `--reviewer <name>`을 사용한다.
검토와 audit은 local model이 꺼져 있어도 동작하며 operative state를 바꾸지 않는다.

기본 offline fake는 모든 결과를 `unclear`로 남기고 어떤 확인도 추론하지 않는다.

## 2026-08-15 실제 Orin probe

격리된 임시 DB에서 다음 장면 하나를 확인했다.

- 이전 사용자: `같은 오류가 반복돼서 너무 답답해`
- 실제 fake response: `fake: 같은 오류가 반복돼서 너무 답답해`
- 다음 사용자: `아니, 지금은 위로보다 해결 방법을 물어본 거야`

Orin local evaluator 결과:

- `outcome=corrected`
- `confidence=0.8`
- evidence exact match
- `intent_match=unclear`
- `response_usefulness=unclear`

수직 단면과 명시적 correction 감지는 동작했지만, 명시적 intent correction을
`contradicted`로 확정하지 못했다. 한 장면 probe는 품질 기준선이 아니며 activation gate를
통과한 것으로 보지 않는다.

초기 locked v1은 4/7 gates에 그쳤다. Memory provenance를 Core와 DB v2에서 보존하고
Improvement를 categorical 결과에서 파생하도록 바꾼 뒤 locked v1 회귀와 새로운 locked v2가
모두 7/7 synthetic gates를 통과했다. locked v2 전체 scene run은 18/30이므로 세부 categorical
품질은 아직 부족하다. 세부 측정은 [Outcome evaluation](outcome-evaluation.md)을 따른다.

## 고도화 gate

- synthetic held-out scene: acceptance, correction, rejection, continuation, abandonment,
  ambiguous topic shift, helpful/unhelpful, memory confirm/contradict, improvement confirm
- schema/exact-evidence completion `>= 95%`
- explicit correction에서 intent contradiction recall `>= 90%`
- ambiguous/topic-shift를 accepted로 만드는 false positive `0`
- memory confirmation precision `>= 95%`
- 전체 memory signal precision과 recall `>= 95%`
- improvement confirmation precision `>= 95%`, recall `>= 90%`
- 동일 입력 반복 시 주요 outcome 재현

synthetic gate 뒤에도 실제 Shadow 대화 30개 이상의 수동 audit를 통과하기 전에는 Outcome으로
Memory를 승인·폐기하거나 응답 정책을 자동 변경하지 않는다. 첫 activation은 자동 수정이
아니라 검토 가능한 candidate 제안으로만 제한한다.
