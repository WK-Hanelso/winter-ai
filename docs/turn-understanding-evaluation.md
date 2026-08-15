# TurnUnderstanding Held-out Evaluation

- 평가일: 2026-08-15
- endpoint: Orin local llama-server (`http://127.0.0.1:18080`)
- 모델 경로: 현재 운영 `A.X-4.0-Light-Q4_K_M.gguf`
- 데이터: prompt example과 문장이 겹치지 않는 synthetic Korean scene 10개
- 실행: response 경로 및 운영 DB와 분리된 manual model evaluation

## 목적

JSON이 생성됐다는 사실이 아니라, `TurnUnderstanding`의 각 필드를 실제 ResponseContract나
Memory에 연결해도 되는지 측정한다. 다음 실패를 별도로 본다.

- schema 또는 exact-evidence 위반
- intent/need 개념 누락
- 순간 상태와 안정 상태의 temporal scope 혼동
- 직접 선호·결정·open loop·improvement signal 누락
- 질문·모호한 반응·제3자 사실의 false memory proposal

## 평가 장면

평가기는 [turn_understanding_evaluation.py](../src/companion/turn_understanding_evaluation.py)의
고정 scene을 사용한다.

1. 직접 말한 안정적 설명 선호
2. 오늘의 순간 피로
3. 근거가 없는 과거 선호 회상 질문
4. 앞선 제안에 대한 모호한 반대
5. CLI와 Voice의 작업 순서 결정
6. 내일 이어갈 open loop
7. 겨울이의 반복 해석 문제
8. 위로가 아니라 해결을 원한다는 intent correction
9. 이해와 공감을 먼저 원하는 지속 선호
10. 제3자 민수에 대한 사실

## 기준선과 변경

| Run | 설정 | Scene | Check | 관찰 |
| --- | --- | ---: | ---: | --- |
| Baseline | prompt + 일반 대화 sampling | 2/10 | 14/24 | JSON extra field, key 이름·요약 evidence가 다수 발생 |
| Structured | llama.cpp JSON Schema | 2/10 | 34/46 | 형식 완료 9/10, 의미·시간 범위 누락이 드러남 |
| Deterministic | JSON Schema + temperature 0 + seed 42 | 4/10 | 42/50 | 출력 재현성과 제3자 memory safety 개선 |
| Final | deterministic + 일반 policy few-shot + 수정된 통합 채점 | **5/10** | **37/45** | 형식 실패 1건, 의미·temporal 누락 7건 |

항상 두 번째 LLM review pass를 실행하는 방식도 네 개 실패 장면에서 측정했다. 결과는
`1/4 scene, 10/15 check`로 단일 pass와 같았고 시간만 약 두 배가 되어 제거했다. validation
error를 넣은 조건부 repair도 같은 잘못된 evidence를 반복해 제거했다. 실패를 숨기는
정규식 보정이나 fake fallback은 추가하지 않았다.

## 최종 장면 결과

| Scene | Result | 핵심 결과 |
| --- | --- | --- |
| stable preference | FAIL | scope와 intent는 맞지만 preference proposal 누락 |
| transient affect | PASS | `turn`, 피로 affect, memory 없음 |
| unknown recall | PASS | recall need와 uncertainty, memory 없음 |
| ambiguous disagreement | FAIL | 요약 evidence를 써 exact-substring 검증 실패 |
| project decision | FAIL | intent는 맞지만 `turn`, project signal·decision proposal 누락 |
| deferred open loop | FAIL | open loop는 맞지만 temporal scope가 `turn` |
| capability gap | PASS | improvement signal과 문제 의도 포착 |
| intent correction | PASS | 해결 요청, memory 없음 |
| support preference | FAIL | 선호 intent는 맞지만 `turn`, preference proposal 누락 |
| third-party fact | PASS | 제3자 선호를 천우 memory로 만들지 않음 |

## Activation gate

한 번의 작은 synthetic set을 실제 품질의 확정값으로 보지는 않는다. 하지만 현재 값을
운영 필드 활성화의 최소 gate로도 사용할 수 없다. Response나 Memory에 연결하기 전 다음을
모두 만족해야 한다.

- schema/exact-evidence completion `>= 95%`
- temporal scope accuracy `>= 90%`
- required project/open-loop/improvement signal recall `>= 90%`
- false stable-memory proposal `0`
- 직접 안정 선호·결정 memory proposal recall `>= 90%`
- 같은 입력을 반복했을 때 주요 분류가 바뀌지 않음

현재 `memory_proposals`와 `temporal_scope`는 **Shadow-only**다. 자동 Memory, ResponseContract,
DialogueDirector에 주입하지 않는다.

## 재현

```bash
PYTHONPATH=src:. python3.11 experiments/turn_understanding_eval.py \
  --url http://127.0.0.1:18080 \
  --output /tmp/turn-understanding-heldout.md

# 특정 실패 장면만 재검증
PYTHONPATH=src:. python3.11 experiments/turn_understanding_eval.py \
  --scene project_decision --scene support_preference
```

평가 실패가 하나라도 있으면 runner는 exit code 1을 반환한다.

## 다음 단계

Order 1의 비개입 구현과 첫 품질 baseline은 완료됐지만 activation gate는 실패했다. 다음
`OutcomeEvaluator`는 천우의 correction·acceptance를 이전 hypothesis에 연결하는 별도
Shadow event로만 구현할 수 있다. 현재 memory proposal을 자동 승인하거나 모델 weight를
갱신하지 않는다.
