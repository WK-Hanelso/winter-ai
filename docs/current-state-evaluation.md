# Current-state Candidate Held-out Evaluation

## 목적

현재 상태 후보 extractor를 prompt 감각으로 반복 수정하지 않고 같은 장면에서 비교한다.
이 평가는 `current_state:*` Memory를 자동으로 활성화하는 gate가 아니다. 합격해도 실제
천우 대화에서 review-only 후보를 수집하고 사람이 audit할 수 있는 자격만 얻는다.

## 평가 세트

서로 목적이 다른 synthetic Korean scene 두 묶음을 사용한다.

- `development`: 첫 실행 뒤 실패 원인을 진단하고 구조를 개선하는 14개 장면
- `locked-v1`: development와 다른 표현·label을 쓰며 첫 일반화 기준선으로 고정한 12개 장면

development 세트는 다음으로 구성한다.

- 상태 변화·지속 4개
- 서로 다른 기존 topic 두 개가 한 문장에 있는 장면 1개
- 직전 겨울이 질문을 이용해야 하는 대명사 장면 1개
- 질문·추측 1개
- 과거 회상 1개
- 제3자 상태 1개
- 미래 가정 1개
- 상태 저장에 관한 기술 질문 1개
- 하루의 단일 사건 1개
- 장기 대화 선호 1개
- 맥락 없는 대명사 1개

positive scene 6개에는 `(topic, accepted evidence)` 7쌍이 있고, negative scene 8개에는
어떤 상태 후보도 없어야 한다. locked-v1도 positive 6개와 negative 6개이며 positive에는
7개 topic이 있다. 각 결과는 기존 active Memory ID와 연결됐는지, evidence가 현재 사용자
원문의 정확한 연속 문자열인지도 별도로 검사한다.

모델이 임의의 evidence를 생성하지 않도록 현재 계약은 후보 본문과 evidence를 시스템이
사용자 발화 전체로 고정한다. 따라서 평가는 `topic` 정확도와 안전하게 수용 가능한
`evidence` 정확도를 분리한다. 짧은 핵심 절뿐 아니라 글자 하나 바꾸지 않은 사용자 발화
전체도 안전한 evidence로 인정한다.

## Candidate audit gate

다음을 모두 만족해야 한다.

1. 선택한 전체 suite의 extraction이 모두 완료된다.
2. 형식 오류로 제외된 candidate warning이 0개다.
3. negative scene에서 false positive가 0개다.
4. topic precision과 accepted-evidence precision이 100%다.
5. topic recall과 accepted-evidence recall이 각각 80% 이상이다.
6. 전체 장면 중 최소 `N-2`개가 모든 check를 통과한다.

이 gate는 실제 후보 audit 시작 조건일 뿐이다. 자동 Memory 활성화, 새 topic 생성,
DialogueDirector 또는 응답 prompt 연결을 허용하지 않는다.

## 실행

대화용 Orin 주소를 기본값으로 사용하지 않는다. 안전한 별도 local llama-server를 명시해야
하며, 주소가 없으면 runner는 실행을 거부한다.

```bash
PYTHONPATH=src python3.11 experiments/current_state_eval.py \
  --url http://127.0.0.1:18081 \
  --suite development \
  --output review/current-state-eval/report.md
```

잠금 세트는 `--suite locked-v1`으로 실행한다. 첫 결과를 본 뒤 그 결과에 맞춰 prompt나
기준을 수정하지 않는다. 다음 시스템 version과 비교할 때 같은 locked-v1을 다시 실행한다.

또는 환경 변수로 지정한다.

```bash
export WINTER_ANALYSIS_LLM_URL=http://127.0.0.1:18081
PYTHONPATH=src python3.11 experiments/current_state_eval.py
```

장면 하나만 진단할 때는 `--scene wellbeing_improved`처럼 선택한다. 출력 report는 scene별
기대값, 관찰값, latency, 실패 check와 aggregate precision/recall을 함께 기록한다.
일부 장면만 실행한 결과는 점수가 좋아도 전체 candidate-audit gate를 통과할 수 없다.

## Qwen3-4B CPU baseline — 2026-08-17

대화용 Orin과 Voice GPU를 사용하지 않고, 별도 CPU-only Qwen3-4B-Instruct-2507 Q4_K_M
llama-server를 `127.0.0.1:18081`에 임시로 띄워 실행했다. 모든 실행 뒤 컨테이너는
종료했으며 실제 active Memory와 current-state Timeline은 변경하지 않았다.

### 최초 development baseline

| 지표 | 결과 | gate |
| --- | ---: | ---: |
| 완전 통과 scene | 7/14 | 12/14 이상 |
| exact update precision | 20.0% | 100% |
| exact update recall | 14.3% | 80% 이상 |
| unexpected exact output | 4 | 0 |
| negative-scene safety | 실패 | 통과 |
| candidate audit gate | **실패** | 통과 |

전체 scene latency 합계는 약 312.5초였다. 첫 positive 장면은 model warm-up을 포함해
57.61초, 이후 장면은 주로 14.88~26.77초였다.

실패는 다음 네 부류다.

1. `wellbeing_improved`는 topic은 맞았지만 기대한 최소 evidence 대신 문장 전체를 골랐다.
2. 회사 스트레스 감소를 더 구체적인 `work_learning_stress`가 아니라 `wellbeing`에 연결했다.
3. 두 topic이 있는 문장을 하나의 wellbeing 후보로 합쳐 공부 부담 변화를 놓쳤다.
4. 맥락 없는 “그건 이제 괜찮아”를 wellbeing 변화로 확정했다.

또한 학습 부담 지속과 미래 가정 장면에서는 model이 원문에 없는 evidence span을 반환해
strict parser가 extraction 전체를 실패시켰다. 이는 잘못된 후보가 저장된 것은 아니지만
candidate audit gate에서는 완료 실패로 계산한다. 질문, 과거 회상, 제3자, 기술 질문,
하루 운동, 장기 설명 선호는 상태 후보로 오인하지 않았다.

이 baseline은 자동 반영 금지와 사용자 검토 gate가 필요하다는 근거다. 원본 report는
개인 review 경로에 저장되며 `/review/` Git 제외 정책을 따른다.

평가기 자체의 deterministic perfect/failure fixture, false-positive 안전 판정, 이전
Memory link 검증과 분석 URL 미설정 거부 테스트도 통과했다. 대화용 Orin은 과거 구조화
분석 부하에서 보드 reset이 관찰됐으므로 이 평가에 자동 재사용하지 않는다.

raw stage trace를 추가로 수집하자 실패 원인은 모델 한 번의 오답보다 계약 전반에 있었다.

1. 순간 상태를 current-state와 장기 semantic 양쪽에 동시에 제안했다.
2. 장기 verifier도 순간 상태를 durable로 잘못 승인했다.
3. evidence를 원문에서 복사하지 않고 이전 저장 상태나 잘린 문자열로 생성했다.
4. verifier가 선택된 이전 topic 하나만 봐서 더 구체적인 topic과 비교하지 못했다.
5. 맥락 없는 대명사의 grounding을 독립 필드로 검사하지 않았다.

이에 따라 state evidence 생성권을 모델에서 제거하고 사용자 발화 전체로 고정했으며,
state로 분류된 발화가 장기 Memory로 중복 유입되는 경로를 닫았다. state verifier에는 모든
active topic을 전달하고 사용자 본인·가장 구체적인 topic·대명사 grounding·현재성·비가정성을
각각 boolean으로 검증하도록 계약을 분리했다.

### 계약 v2 development 재평가

| 지표 | 결과 | gate |
| --- | ---: | ---: |
| 완전 통과 scene | 8/14 | 12/14 이상 |
| topic precision | 50.0% | 100% |
| topic recall | 28.6% | 80% 이상 |
| accepted-evidence precision | 50.0% | 100% |
| accepted-evidence recall | 28.6% | 80% 이상 |
| unsafe negative update | 2 | 0 |
| extraction 실패 | 1 | 0 |
| candidate audit gate | **실패** | 통과 |

질문·과거 회상·제3자·미래 가정·기술 질문·하루 사건·장기 선호는 모두 안전하게 거절했다.
명시적인 wellbeing 개선도 잡았다. 반면 `아직`·`여전히` 지속 상태, 회사·학습 topic,
recent context로 이어지는 대명사를 놓쳤다. 두 topic 문장은 하나의 work update로 합쳤고,
맥락 없는 대명사는 wellbeing과 work 두 topic에 동시에 연결했다. 한 장면에서는 JSON
Schema 제한에도 model이 0~1 밖의 confidence를 반환해 strict parser가 실패로 기록했다.

### locked-v1 첫 실행

| 지표 | 결과 | gate |
| --- | ---: | ---: |
| 완전 통과 scene | 6/12 | 10/12 이상 |
| topic precision | 50.0% | 100% |
| topic recall | 28.6% | 80% 이상 |
| accepted-evidence precision | 50.0% | 100% |
| accepted-evidence recall | 28.6% | 80% 이상 |
| unsafe negative update | 2 | 0 |
| extraction 실패 | 0 | 0 |
| candidate audit gate | **실패** | 통과 |

development와 다른 문장에서도 같은 실패가 재현됐다. 명시적인 wellbeing 회복은 잡았지만
지속 상태, 회사·학습 변화, context 대명사를 놓쳤고 두 topic을 하나로 합쳤다. 맥락 없는
대명사는 두 topic에 동시에 잘못 연결했다. 따라서 현재 병목은 특정 예문 암기가 아니라
작은 모델의 topic grounding·복합 절 분해·지속 상태 판정이다. locked-v1 결과를 본 뒤
추가 prompt tuning이나 재실행은 하지 않았고 첫 일반화 기준선으로 고정했다.

첫 locked-v1 결과를 고정한 뒤, development에서도 이미 확인됐던 무근거 대명사 경로에는
직전 맥락이 없으면 후보를 거부하는 deterministic 안전 guard를 추가했다. 이 guard는
기억 내용을 결정하지 않고 불충분한 근거를 거절하기만 한다. locked-v1은 이 변경 뒤 다시
실행하지 않았으므로 위 수치는 오염되지 않은 첫 기준선이다.

그 다음 development에서 관찰한 invalid confidence를 대상으로 후보별 오류 격리를
추가했다. 상태 후보 배열의 한 항목만 fields, active topic, duplicate topic 또는 confidence
계약을 어기면 그 항목만 제외하고 정상 항목은 계속 검증한다. 제외 사실은 extraction을
`completed`로 기록할 때 warning으로 함께 보존하고 `./winter overview`에 발화 ID와 제외
이유를 표시한다. 전체
JSON 파손과 local model 연결 실패는 격리 대상이 아니며 기존처럼 실패 또는 보류 후
재시도한다. warning이 하나라도 있는 평가 실행은 candidate-audit gate를 통과하지 못한다.
이 변경은 deterministic acceptance test로 확인했고 첫 locked-v1 수치는 재실행하지 않았다.

recall 개선은 단어 목록 하드코딩이 아니라
`상태 지속`, `복합 절 분해`, `topic grounding`을 별도 판단 단계나 더 적합한 local model로
나누어 비교한다. gate 통과 전에는 실제 후보 audit도 자동 활성화도 시작하지 않는다.
