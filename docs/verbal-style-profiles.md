# 말투 profile

`VerbalStylePlanner`가 쓰는 말투 정책이다. `configs/verbal_style/*.py`에 데이터로
두어 사람이 읽고 비교하고 교체할 수 있게 한다.

관련: [reference-speech-style.md](reference-speech-style.md),
[ADR-0002](adr/0002-coupled-human-reference-baseline.md) 결정 5·6.

---

## 1. 왜 profile인가

이전 정책은 손으로 쓴 영어 지시문 세 줄이었고 근거가 없었다. 대부분의 turn에는
지시문이 아예 없었다.

profile로 분리하면 세 가지가 가능해진다.

- 무엇 때문에 그렇게 말하는지 파일 하나로 확인할 수 있다
- 같은 입력에 대해 이전 정책과 Reference 정책을 비교할 수 있다
- 측정이 갱신되면 코드가 아니라 데이터를 고친다

## 2. profile

| profile | 용도 |
| --- | --- |
| `reference_broadcast` | **기본값.** #90 측정에서 두 전사가 합의한 특성 |
| `base` | 이전 정책. 비교 기준으로만 유지 |

`base`는 예전에 실제로 동작하던 형태를 그대로 재현한다. 일반 turn에는 지시문을
내지 않는다(`shared_instruction: False`). 정리된 버전이 아니라 실제로 배포됐던
동작과 비교해야 의미가 있다.

## 3. reference_broadcast의 근거

모든 값이 [reference-speech-style.md](reference-speech-style.md)의 측정으로
추적된다. 두 독립 전사가 합의한 것만 넣었다.

| 설정 | 측정 근거 |
| --- | --- |
| `register: mostly_plain` | 존댓말 비율 0.312 / 0.308 — 반말이 우세하되 존댓말도 나타난다 |
| `max_sentences: 1`, `max_words_per_sentence: 8` | 평균 발화 3.00 / 2.69 단어. 문장 수만으로는 길이가 통제되지 않아 단어 상한을 함께 둔다 ([평가](style-holdout-evaluation.md) §4) |
| 격식체 미사용 | 35분간 격식체 종결 각 1회 |
| `discourse_markers: ("근데",)` | 양쪽 전사 상위 어휘 |
| `hesitation_markers` | `뭔가` 양쪽 1위, 이어서 `약간`·`진짜`·`그러니까` |
| `hesitation_usage: sparing` | 필러 2.64 / 3.79 per 100단어 — 드물다 |

### 저장소에 넣지 않는 것

**고유명사, 별명, 특정 인물·사건을 지시하는 표현은 profile에 넣지 않는다.**
측정에서 나온 그런 어휘는 외장 storage의 report에만 남는다. profile에는 일반
어휘와 register 특성만 둔다.

## 4. 지시문 구성 순서

register가 항상 먼저다. 뒤에 다른 지시가 경쟁할 때 모델이 가장 먼저 놓치는 것이
어투이기 때문이다.

```
register → 길이 제한 → 담화 표지 → 머뭇거림 → dialogue act별 지시
```

`shared_instruction: False`인 profile은 앞의 네 가지를 생략하고 act 지시만 낸다.

## 5. system 메시지 순서

말투 지시가 항상 붙게 되면서 기존의 잠재 결함이 드러났다. `core.py`가 메시지를
반복 prepend하고 있어 나중에 추가된 것이 앞에 왔고, Identity가 style·memory 뒤로
밀렸다.

지금은 한 곳에서 명시적 순서로 조립한다.

```
[identity, memory, style, grounding, ...대화 이력]
```

- **identity가 먼저다.** 나머지 전부를 규정하기 때문이다
- **style이 마지막이다.** 생성 지점에 가장 가까워 어투 지시가 덜 희석된다

테스트로 고정했다.

## 6. 실행

```bash
docker compose run --rm dev python -m companion.cli --backend fake \
  --verbal-style reference_broadcast --prompt "안녕"
```

profile 이름이 잘못되면 즉시 실패한다. 기본 말투로 조용히 넘어가지 않는다.
어투가 틀린 채로 대답하는 것이 시작을 거부하는 것보다 나쁘다.

## 7. 실측 비교 (2026-08-09, Qwen3-4B-Instruct, 로컬)

같은 질문 "오늘 뭐 하고 지냈어?"에 대한 응답이다.

**`base`**: 존댓말, 여러 문단, 이모지와 목록, AI임을 밝히는 설명이 붙었다.

**`reference_broadcast`**: `근데 오늘은 그냥 집에서 쉬다가 라면 먹었어.` 한 문장.

반말과 길이 제한이 실제로 적용된다. `근데`는 매 응답에 나타나지 않았다. 다른
질문에서는 붙지 않았으므로 과잉 적용은 관찰되지 않았다.

## 8. 알려진 한계

- **방송 register에서 관찰된 말투다.** 다수 청자를 향한 발화이며 1:1 대화와 다를 수
  있다. 대화 source로 보정하는 것은 관계 정보 단계의 일이다
- **응답 품질을 정량 평가하지 않았다.** held-out 평가는 별도 issue다
- 위 실측에서 Reference profile은 개인적 경험을 지어냈다(`라면 먹었어`). 확인 결과
  **identity를 로드해도 막히지 않았고 오히려 더 구체적으로 지어냈다.** 별도의
  [grounding 정책](grounding-policy.md)으로 처리했다
- prosody와 통합되지 않았다. Joint Utterance Planner는 후속이다
- `ADR-0002` 결정 6: Human Reference는 최종 겨울이가 아니다. 이 profile은 기준선이다

## 9. 측정된 성능

[held-out 평가](style-holdout-evaluation.md) 기준 스타일 거리(낮을수록 가까움).

| profile | 평균 거리 (3회) |
| --- | ---: |
| `base` | 0.619 |
| `reference_broadcast` | 0.252 |
| 바닥값 (Reference 자기 거리) | 0.162 |

남은 거리의 대부분은 **존댓말 비율**이다. 모델은 범주 선택을 규칙으로 다루므로
"넷 중 하나는 존댓말" 같은 분포가 프롬프트로 유도되지 않는다.
