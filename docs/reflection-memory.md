# Evidence-backed Conversation Memory Recovery

## 사용자가 경험하는 변화

이전에는 대화 원문이 SQLite에 남아 있어도 겨울이가 다음 대화에서 사용할 수 있는
장기 기억으로 정리되지 않았다. 이제 다음 세 단계를 분리한다.

```text
보존된 대화 원문
  → local LLM의 맥락 기반 장기 정보 판정
  → 실제 원문 근거가 붙은 검토 후보
  → 천우가 승인한 후보만 active Memory
```

`./winter overview`는 각 단계의 수를 보여 주고, `./winter memories`는 후보를 승인,
수정 후 승인, 거절하거나 나중으로 넘길 수 있다. 프로그램을 다시 시작해도 상태는
SQLite에 남는다.

## 안전 계약

- 대화 원문은 분석 상태와 무관하게 `conversations.sqlite`에 보존한다.
- 후보 본문과 evidence는 현재 사용자 발화 원문을 그대로 사용한다.
- 최근 대화 최대 6개는 의도 구분에만 사용하며 후보의 근거로 복사하지 않는다.
- local LLM은 장기 정보의 종류와 시간 범위를 판정하지만 새 기억 문장을 작성하지 않는다.
- 1차 추출 뒤 `직접 진술`, `지속성`, `순간 지시 아님`을 독립적으로 재검증한다.
- 한 발화에서 최대 한 후보만 제안한다.
- 후보는 자동으로 active Memory가 되지 않는다.
- 안정적인 `semantic`, `preference`, `decision`, `project`, `procedural`만 사용자 승인 뒤
  active Memory가 될 수 있다.
- 사용자 확인을 받은 현재 상태는 `current_state:<topic>`으로 활성화한다. 같은 topic의
  새 값은 이전 값을 supersede/deprecate하고, 모델에는 영구 특성이 아니라 현재 상태라는
  설명과 함께 전달한다.
- `./winter state`는 topic별 현재·과거 값을 시간순으로 보여 준다. `created_at`은 해당
  상태의 시작, deprecated 상태의 `updated_at`은 다음 값으로 교체된 시점으로 사용한다.
- 일반 대화에는 active 상태만 전달한다. 과거·변화·비교와 상태 표현이 함께 있는 질문에만
  최대 10개의 current-state History를 별도 Timeline context로 전달한다.
- Timeline은 관찰된 이전/현재 값만 제공하며 모델이 변화의 원인을 추측하지 못하게 한다.
- 기능 도입 뒤 새 발화만 현재 상태 변경 후보로 분석한다. 기존 분석 backlog를 지금
  상태로 재해석하지 않는다.
- 상태 변경 후보는 이미 active인 topic만 고를 수 있다. 모델은 topic만 제안하고 시스템이
  현재 사용자 발화 전체를 evidence와 후보 본문으로 고정한다.
- 별도 검증자가 사용자 자신의 상태인지, 전체 active state 중 가장 구체적인 topic인지,
  대명사가 최근 대화에 근거하는지, 현재까지 이어지는 상태인지, 질문·가정이 아닌지를
  독립 필드로 확인한다.
- “그건”, “그 부분”처럼 대상이 문장 안에 없는 표현은 직전 대화 맥락도 없으면 verifier를
  호출하기 전에 fail-closed로 거절한다. 이 규칙은 상태를 만들지 않고 근거 없는 연결만 막는다.
- 상태 후보 하나의 fields, topic, 중복 또는 confidence가 잘못돼도 다른 정상 후보는 잃지
  않는다. 잘못된 항목만 제외하고 extraction warning을 SQLite에 남긴다. overview에는 최근
  경고의 발화 ID와 제외 이유도 함께 보인다.
- 전체 JSON이 깨지거나 local model이 끊긴 경우는 후보별 오류로 축소하지 않는다. 분석을
  실패 또는 보류 상태로 남기고 다음 실행에서 다시 시도한다.
- 한 발화가 current-state로 분류되면 같은 원문을 장기 semantic Memory로 중복 제안하지
  못한다. state verifier가 최종 거절해도 순간 상태가 장기 정보로 우회 유입되지 않는다.
- 같은 topic에 미검토 후보가 여러 개면 가장 최근 사용자 발화만 남긴다.
- 천우가 기존 상태와 새 근거를 확인하고 승인한 뒤에만 이전 상태를 deprecated History로
  옮기고 새 상태를 active로 만든다.

## 실제 복구 상태와 검증

2026-08-15 기준 원문 230개 중 사용자 발화는 126개다. 모든 사용자 발화가 원본 message
ID와 Shadow turn event에 연결됐고, 이전에 연결되지 않았던 79개도 비파괴적으로
등록됐다. 원문을 지우거나 다시 작성하지 않았다.

같은 날 맥락 포함 최종 정책으로 최근 사용자 발화 40개를 실제 CPU 모델에서 다시
분석했다. 실패는 0개, 검토 후보는 5개였다. 엔지니어링 사전 검사에서는 3개가 안정적인
선호·업무·신상 후보로 적절했고, 2개는 순간 상태를 안정 정보로 본 분류 오류라
수정 또는 거절이 필요했다. 명확한 SWM 재직 진술 하나를 놓친 recall 실패도 확인했다.
이는 자동 활성화 기준을 통과한 결과가 아니라 사용자 검토가 필요한 Level 3 실패
범위를 측정한 baseline이다. live 대화가 계속 추가되어 2026-08-17 기능 검증 시점의
overview는 원문 236개, 사용자 발화 132개, active Memory 9개였다. 이 수치는 사용에 따라
계속 증가한다.

## Current-state 후보 수직 단면

Reflection DB schema v2는 상태 후보에 `state_topic`과 이전 active Memory ID를 함께
보존한다. migration 시각을 cutoff로 기록해 그보다 오래된 대화는 장기 기억 분석만 하고
현재 상태 분석은 하지 않는다. 대화 DB의 시각 정밀도가 초 단위이므로 cutoff 비교도 같은
정밀도로 맞췄다.

새 발화의 현재 상태 후보는 다음 순서로 처리한다.

```text
새 사용자 발화 + active current_state snapshot
  → 기존 topic 중 변화·지속 후보 추출
  → exact evidence / 사용자 본인 / topic 일치 / 현재성 재검증
  → review-only candidate
  → 천우 승인
  → 이전 active deprecated + 새 active
```

### 실제 사용할 때 내부 반응

대화 응답과 기억 분석은 동기적으로 붙지 않는다. 천우가 CLI, Web이나 Voice에서 말하면
그 발화와 겨울이의 답변은 즉시 원문 DB에 저장되고 Shared Core가 내부 분석 queue에 자동
등록한다. 같은 응답 도중에 current-state가 바뀌지는 않으며 겨울이는 그 시점에 이미
확정된 active Memory로 먼저 답한다.

현재 maintenance policy는 평가 gate 실패 때문에 `shadow`다. queue 등록은 자동이며
TurnUnderstanding pending event만 계속 소비하는 별도 background Worker와 재시작·backoff
계약도 구현했다. 다만 안전한 전용 분석 runtime과 supervisor는 아직 상시 배포하지 않았다.
현재 Worker는 Reflection/current-state 후보와 Outcome queue까지 소비하지 않는다.
`./winter reflect`는 개발자가 같은 내부 경로를 진단하는 도구일 뿐 최종 사용자 절차가
아니다. 자세한 운영 경계는 [Shadow Maintenance Worker](maintenance-worker.md)를 따른다.
향후 Reflection Worker가 queue를 소비하면 다음 순서가 시작된다.

1. 아직 완료되지 않은 최신 사용자 발화를 고르고 직전 대화 최대 6개를 함께 읽는다.
2. migration cutoff 이전 발화는 현재 상태로 재해석하지 않는다.
3. 현재 active인 `current_state:*` snapshot의 topic, Memory ID와 내용을 읽는다.
4. local model은 장기 정보 후보 최대 1개와 기존 상태 topic 후보 최대 3개를 JSON으로 제안한다.
5. 상태 후보가 하나라도 나온 발화는 같은 문장을 장기 semantic Memory로 중복 저장하지 않는다.
6. 각 상태 항목의 schema를 독립 검사한다. 잘못된 항목은 warning으로 격리하고 정상 항목은
   계속 처리한다.
7. 맥락 없는 대명사는 코드 안전 guard에서 먼저 거절한다.
8. 정상 후보마다 사용자 본인, 가장 구체적인 topic, grounding, 현재성, 질문·가정 아님을
   별도 local-model 호출로 검증한다.
9. 통과한 후보의 내용과 evidence는 model 문장이 아니라 실제 사용자 발화 전체로 고정하고,
   분석 당시 active Memory ID를 `supersedes` 대상으로 붙인다.
10. SQLite에는 review-only candidate로 저장한다. 같은 topic의 미검토 후보가 또 생기면 가장
    최근 발화의 후보만 남긴다.
11. candidate mode의 불확실한 정보는 겨울이가 다음 대화에서 자연스럽게 확인한다. 개발자는
    `./winter memories`로 같은 근거와 상태 전이를 audit할 수 있지만 천우의 정상 사용
    절차는 아니다.
12. operative mode에서 직접 진술·근거·정책 gate가 모두 맞으면 기존 값은 deprecated
    History가 되고 새 값만 active가 된다. 불확실하거나 충돌하면 상태는 그대로다.
13. 다음 대화부터 CompanionCore는 새 active 상태를 사용한다. 과거 Timeline은 천우가 변화나
    비교를 물을 때만 별도로 제공한다.

따라서 목표 실사용 계약은 `대화 → 즉시 응답 + 내부 queue → background 분석 → 확실하면
내부 최신화 / 애매하면 겨울이가 대화로 확인 → 다음 대화부터 반영`이다. held-out gate가
실패한 현재는 queue와 mode 기록까지만 자동이고 Memory 반영은 Shadow로 차단한다.

deterministic adapter·repository acceptance test에서 승인 전 불변, 승인 후 supersedes 연결,
기존 backlog 차단, topic별 최신 미검토 후보 유지와 schema v1→v2 비파괴 migration을
확인했다. 정확한 최신 회귀 수는 [V1 Maturity Ledger](v1-maturity-ledger.md)에 기록한다.

후속 단계로 development 14-scene과 표현이 겹치지 않는 locked-v1 12-scene evaluator를
추가했다. topic/evidence precision 100%, recall 80% 이상, negative false positive 0을
실제 후보 audit 시작 조건으로 삼는다. 임시 CPU-only Qwen3-4B에서 계약 개선 뒤
development는 8/14, locked-v1 첫 실행은 6/12였고 두 세트 모두 topic precision 50.0%,
recall 28.6%, unsafe negative update 2로 gate에 실패했다. 자세한 장면별 실패와 최초
baseline부터의 계약 변경은 [Current-state held-out 평가](current-state-evaluation.md)를
따른다.

초기 compact extractor는 순간 감정을 project로 분류하고 질문에서 존재하지 않는 사실을
만드는 실패가 있었다. 다음 순서로 구조를 고쳤다.

1. 장기 기억 종류를 사실·선호·결정·프로젝트·지원 방식으로 제한했다.
2. 한 발화당 최대 한 후보만 허용했다.
3. 별도 검증 판단을 추가했다.
4. 생성 요약을 폐기하고 실제 사용자 원문을 후보 본문으로 사용했다.
5. 최근 대화 맥락을 의도 구분에 포함했다.

고정 반례 probe에서 다음 결과를 확인했다.

| 입력 성격 | 기대 | 결과 |
| --- | --- | --- |
| 회사 재직 직접 진술 | semantic 후보 | 통과 |
| 좋은 E2E 구조를 묻는 질문 | 후보 없음 | 검증 단계에서 거절 |
| 겨울이가 더 묻고 궁금해하길 바라는 직접 요청 | preference 후보 | 통과 |

## Runtime 안전성

Orin의 대화 모델에 중간 크기 구조화 분석 요청을 반복했을 때 llama-server만 종료된 것이
아니라 보드 uptime이 초기화되고 `SYS_RESET_N`이 기록됐다. 원인은 아직 확정하지 않았지만
전원·하드웨어 reset 가능성이 있어 추가 부하 요청을 중단했다.

`./winter reflect`와 다른 사후 분석 명령은 이 기록 때문에
`WINTER_ANALYSIS_LLM_URL`이 명시되지 않으면
실행을 거부한다. 현재 실제 복구 검증은 RTX 2060의 실행 중인 Voice 서비스를 건드리지
않기 위해 별도 CPU-only Qwen3-4B llama-server에서 수행했다.

## 알려진 한계와 다음 고도화

- Qwen3-4B의 시간 범위·종류 분류는 완벽하지 않아 사람 검토가 필요하다.
- 40개 실제 표본 사전 검사에서 안정 후보 정밀도는 3/5였고 명확한 사실 누락도 있었다.
- 맥락 6개를 발화마다 반복하는 CPU 직렬 분석은 느리다.
- 후보 간 의미 중복 병합은 아직 없다.
- 순간 경험과 관계 변화는 장기 사실과 별도 episodic/relationship track이 필요하다.
- 자연어 상태 변경을 기존 topic 후보로 연결하는 수직 단면과 local LLM held-out 평가는
  구현됐지만 development와 locked-v1 모두 candidate-audit gate에 실패했다. 실제 대화
  audit과 자동 활성화는 열지 않는다.
- 새 current-state topic 생성·기존 복합 topic 분할·topic alias 병합은 아직 지원하지 않는다.
- “그건 이제 괜찮아” 같은 대명사는 맥락이 없을 때 두 topic으로 잘못 연결됐고, 맥락이
  있을 때는 오히려 놓쳤다. grounding 안전 guard와 context 해석 개선이 모두 필요하다.
- 대화 이해와 답변 결과 평가의 Shadow backlog는 안전한 분석 runtime이 확정되기 전까지
  답변에 연결하지 않는다. TurnUnderstanding 소비 Worker의 코드만 완성됐으며 운영
  supervisor, Reflection/current-state와 Outcome 연결은 남아 있다.
- 무근거 대명사와 후보별 invalid confidence 격리는 완료했지만 real-model suite는 첫 locked
  기준선을 보존하기 위해 다시 실행하지 않았다.
- warning 발화를 새 model/version으로 선택 재분석하는 CLI는 아직 없다. 현재는 원인 확인만
  가능하며 재분석 정책은 model marker와 함께 설계해야 한다.
- 다음 단계는 지속 상태·multi-topic·topic grounding을 prompt 하드코딩이 아닌 별도 판단
  단계 또는 더 적합한 local model로 비교하는 것이다.
