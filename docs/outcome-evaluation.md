# OutcomeEvaluator Calibration and Locked Evaluation

- 측정일: 2026-08-15
- local endpoint: Orin llama-server `http://127.0.0.1:18080`
- 실행 모델: 현재 Orin endpoint의 local model
- 외부 상용 LLM API: 사용하지 않음
- 실제 대화·Memory DB: 사용하지 않음

## 평가 원칙

Outcome 품질만 분리하기 위해 이전 `TurnUnderstanding`은 각 synthetic scene의 고정 fixture로
제공한다. 실제 답변과 다음 사용자 반응만 evaluator가 판정한다.

첫 15개 scene의 실패를 보고 field 정의와 prompt를 교정했으므로 이 세트는 더 이상
held-out이 아니다. 코드에서는 `calibration` suite로 명시한다. 최종 baseline은 prompt
교정에 사용하지 않은 별도 15개 `heldout` suite를 두 번 실행해 측정했다.

```bash
PYTHONPATH=src python3.11 experiments/outcome_eval.py \
  --url http://127.0.0.1:18080 \
  --suite heldout --repeats 2 \
  --output /tmp/winter-outcome-locked-v1.md
```

activation gate를 통과하지 못하면 명령은 의도적으로 exit code 1을 반환한다.

## Calibration baseline과 교정

최초 15 scene × 2회 결과:

- 4/30 runs passed
- 136/190 checks passed
- 기존 5개 gate 중 2개 통과
- schema/exact-evidence completion 26/30, 86.7%
- correction의 intent contradiction recall 2/10, 20%
- ambiguous/topic-shift false acceptance 0/6
- memory confirmation precision 2/2, 100%

관찰된 구조적 실패는 `intent_match`의 비교 대상 혼동, assistant response를 evidence로
복사한 provenance 위반, 일반 지식 정정을 Memory 반박으로 오인한 것이었다. 다음을
교정했다.

- `intent_match`가 실제 답변과 이전 conversational need의 일치임을 명시
- JSON Schema가 모든 evidence로 `next_user_text` 원문만 허용
- corrected/rejected/abandoned의 의미 경계와 우선순위 명시
- 사용자 Memory와 일반 사실 정정을 분리
- improvement signal의 precision/recall gate 추가
- full JSON 동일성이 아니라 outcome·intent·usefulness·affect·signal presence의 주요
  범주 재현으로 determinism 정의

## Locked v1 결과

15개의 새 scene을 두 번 실행한 결과다. 이 결과를 본 뒤 같은 suite에 맞춰 prompt를 다시
수정하지 않았다.

```text
6/30 runs passed
180/212 checks passed
4/7 activation gates passed
30 calls total model time 183.38s
average 6.11s, min 5.39s, max 7.89s
```

| Activation gate | 결과 |
| --- | --- |
| schema/exact-evidence completion `>=95%` | PASS — 30/30, 100% |
| correction intent contradiction recall `>=90%` | PASS — 10/10, 100% |
| ambiguous/topic-shift false acceptance `=0` | PASS — 0/6 |
| memory confirmation precision `>=95%` | FAIL — prediction 0, confirmation 2회 누락 |
| all memory signal precision/recall `>=95%` | FAIL — precision 2/2, recall 2/4, 50% |
| improvement precision `>=95%`, recall `>=90%` | FAIL — precision 0/4, recall 0/14 |
| repeated major outcomes deterministic | PASS — 15/15 scene 두 번 동일 |

## Provenance-scoped redesign

단일 model이 text만 보고 사용자 Memory인지 추측하게 한 구조는 prompt를 분리해도 precision이
33~40%에 머물렀다. 원인은 모델이 아니라 입력 경계였다. Core는 실제 답변에 주입한 active
Memory를 알고 있었지만 Outcome에는 전달하지 않았다.

다음 구조로 변경했다.

```text
categorical local LLM
  └─ outcome / intent_match / usefulness / affect
            │
            ├─ rejected|corrected + direct failure
            │     └─ improvement candidate를 계약으로 파생
            │
            └─ Core가 실제 사용한 Memory ID가 있을 때만
                  memory relation local LLM
                    └─ confirmed / contradicted / none / unclear
```

- Outcome DB schema v2가 turn별 `id/kind/content` Memory provenance를 저장한다.
- v1 DB는 `memory_claims_json='[]'`로 비파괴 migration한다.
- Core가 선택하지 않은 active Memory는 Outcome에 전달하지 않는다.
- Memory provenance가 없으면 두 번째 local model 호출을 생략한다.
- Improvement는 별도 LLM 추측이 아니라 categorical `rejected|corrected`와
  `contradicted|unhelpful|worsened`의 일관된 결합에서 candidate로 파생한다.
- 이 candidate도 아직 실제 ImprovementLedger나 Memory를 변경하지 않는다.

calibration 15 scene의 최종 결과는 10/15 runs, 99/106 checks, 반복 미측정을 제외한 6/7
gate다. Memory와 Improvement precision/recall은 모두 100%였고 비-Memory 장면은 약
4.7초, Memory 장면은 약 7~9초였다.

이미 공개된 locked v1 회귀는 20/30 runs, 198/212 checks, **7/7 gate**를 통과했다.

## Locked v2 최초 결과

locked v1 실패를 본 뒤 새 문장과 다른 Memory kind(`decision`, 개인 semantic fact)로
15 scene을 만들었다. 아래 실행 뒤 같은 suite에 맞춘 추가 prompt 수정은 하지 않았다.

```bash
PYTHONPATH=src python3.11 experiments/outcome_eval.py \
  --url http://127.0.0.1:18080 \
  --suite locked-v2 --repeats 2 \
  --output /tmp/winter-outcome-locked-v2-first-run.md
```

```text
18/30 runs passed
194/212 checks passed
7/7 synthetic activation gates passed
30 runs total model time 155.64s
average 5.19s, min 4.30s, max 8.96s
```

Memory confirmation·contradiction과 Improvement는 precision/recall 100%, false acceptance는
0/6, 주요 categorical output은 15/15 scene에서 두 번 동일했다.

## 실제로 신뢰할 수 있는 범위

- 구조화 출력과 evidence provenance
- 다음 반응이 이전 답변의 의도를 명시적으로 반박했는지
- 명시적인 accepted/rejected/continued/abandoned와 모호한 주제 전환의 큰 구분
- 동일 입력에서 주요 categorical output의 재현
- Core가 실제 사용한 Memory에 한정한 명시적 확인·반박
- 명시적 응답 실패에서의 non-operative Improvement candidate

이 범위도 Shadow 분석 참고치일 뿐 Memory나 실제 응답 정책을 자동 변경할 권한은 없다.

## 실패 범위

- `corrected`와 `rejected`의 세부 label: 대안을 밝힌 correction도 rejected로 분류
- Affect 보수성: 부정 표현을 직접적인 감정 변화 없이 `worsened`로 과잉 일반화
- Ambiguous 세부 필드: 최상위 outcome은 unclear지만 intent/usefulness를 partial/unhelpful로 단정
- continuation 하나를 `unclear`로 놓침
- 성능: 평균 5.19초이고 Memory 평가 시 최대 8.96초로 동기식 대화 경로에는 부적합

## 다음 고도화 판단

synthetic 7개 gate는 통과했지만 운영 activation 조건은 아직 충족하지 않았다. 다음은 실제
대화 Shadow event를 대상으로 한 수동 audit이다.

1. 최소 30개의 자연 대화 Outcome을 개인정보를 외부로 보내지 않고 로컬에서 수집
2. 천우 또는 수동 reviewer가 categorical·Memory·Improvement를 blind 판정
3. Memory/Improvement false positive 0, precision `>=95%`, correction recall `>=90%`
4. 잘못된 candidate를 무시·폐기할 수 있는 review lifecycle과 audit trail 확인
5. 통과 후에도 자동 active Memory 수정이 아니라 `candidate` 제안만 제한적으로 연결

실제 대화 검토 경로는 schema v3에 구현했다. 아래 순서로 분석된 대화를 모델 label을 보지
않고 검토하며, 마지막 명령으로 30개 진행률과 불일치를 확인한다.

```bash
./winter chat --analyze-pending-turns --analysis-limit 10
./winter chat --analyze-pending-outcomes --analysis-limit 10
./winter chat --review-outcomes --review-limit 5
./winter chat --outcome-audit
```

2026-08-15 현재 실제 blind review는 `0/30`이다. 따라서 이 경로가 동작하는 것과
OutcomeEvaluator가 운영에 사용 가능하다는 판단을 구분한다. 검토 저장 후에도 Memory,
DialogueDirector와 ImprovementLedger는 변경되지 않는다.

`corrected/rejected`, continuation과 ambiguous 하위 필드는 별도 calibration 대상으로
유지한다. 실제 Shadow audit 전에는 Outcome으로 Memory·DialogueDirector·ImprovementLedger를
변경하지 않는다.
