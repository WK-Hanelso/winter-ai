# Reference Human Selection

이 문서는 Milestone 4에서 사용할 한 명의 Reference Human을 고르기 위한 데이터 적합성
기준이다. 특정 인물의 유명세, 짧은 인상이나 겨울이와의 유사도를 평가하는 문서가 아니다.

Issue: [#69](https://github.com/WK-Hanelso/winter-ai/issues/69)

## Decision boundary

이 단계에서는 후보를 관찰하고 source의 적합성만 조사한다. 영상·음성을 다운로드하거나
전사, 화자 분리, 모델 학습을 하지 않는다. 실제 인물 이름과 source URL을 공개 GitHub에
기록하지 않는다. 외장 저장소의 private candidate manifest가 실제 인물과 익명
`candidate_id`의 대응을 보관한다.

Git에는 빈 template, 평가 규칙과 익명화된 decision summary만 남긴다.

## What selection means

Reference Human은 인간 전체의 대표나 최종 겨울이가 아니다. 다음 질문에 답할 수 있는
하나의 관찰 가능한 기준점이다.

> 같은 사람의 context, script, voice와 delivery를 충분히 관찰해 보지 않은 상황에서도
> 그 사람다운 다음 행동을 재현하고 평가할 수 있는가?

겨울이에게 원하는 성격과 목소리는 Reference 검증 뒤 Winter Delta로 결정한다. 따라서
`겨울이와 닮았는가`, `천우가 좋아하는가`와 같은 항목은 데이터 품질 점수에 포함하지
않는다.

## Hard gates

아래 조건은 모두 통과해야 한다. 하나라도 실패하면 weighted score와 무관하게 `reject`한다.
확인할 자료가 부족한 경우에는 통과로 추정하지 않고 `hold`한다.

| Gate | Required evidence | Why |
| --- | --- | --- |
| H1 동일 인물 | 여러 source에서 동일 화자임을 직접 확인 | 다른 화자·대역 혼입 방지 |
| H2 결합 관찰 | 같은 scene에서 맥락, script와 실제 음성을 함께 관찰 가능 | text/voice 분리 왜곡 방지 |
| H3 자연 한국어 대화 | 대본 낭독이 아닌 한국어 상호작용 source가 충분함 | CLI behavior 기준 확보 |
| H4 연속 대화 | 심한 jump cut 없는 연속 대화 구간이 여러 source에 존재 | pause, timing, repair 관찰 |
| H5 음성 사용 가능성 | 배경음악·변조·겹침이 적은 단일 화자 구간이 여러 source에 존재 | Reference Voice 평가 가능 |
| H6 관계·상황 범위 | 둘 이상의 interlocutor와 서로 다른 상황을 관찰 가능 | 특정 상대 persona 과적합 방지 |
| H7 기간 고정 가능 | 충분한 자료를 하나의 명시적 source 기간 안에서 구성 가능 | persona·voice drift 축소 |
| H8 자료량 가능성 | 선정 후 10시간 이상 raw 후보, 그중 3시간 이상 상호작용 자료를 확보할 가능성이 있음 | 2~3시간 probe 이후 확장 가능성 |
| H9 추적 가능성 | source ID, 게시일, 관찰 timestamp와 접근일을 기록 가능 | 재현·중복·삭제 추적 |

H8의 시간은 모델 학습에 충분하다는 주장이 아니라, 소규모 probe 뒤 곧바로 자료 부족에
막힐 후보를 걸러내기 위한 운영 기준이다. 실제 usable duration은 수집 probe에서 다시
측정한다.

## Evidence levels

평가자는 관찰과 추정을 분리한다.

| Level | Meaning | Scoring use |
| --- | --- | --- |
| A | 서로 다른 여러 source의 timestamp에서 직접 반복 관찰 | 점수 근거로 사용 |
| B | 하나의 source에서 직접 관찰 | 임시 점수, 추가 확인 필요 |
| C | source metadata 또는 당사자 설명 | 맥락 보조만 가능 |
| D | 인상, 추정 또는 제3자 평가 | 점수 근거로 사용 금지 |

Hard gate는 최소 B evidence가 있어야 하며, 최종 `advance` 전에는 H2부터 H7까지 A evidence를
요구한다.

## Weighted rubric

Hard gate를 통과한 후보만 100점 척도로 비교한다. 각 항목은 evidence timestamp를 함께
기록한다.

| Dimension | Weight | High score evidence |
| --- | ---: | --- |
| Coupled scene availability | 25 | context, script와 voice가 함께 보존된 연속 scene이 다양한 source에 있음 |
| Spontaneity and edit integrity | 20 | 대본·하이라이트보다 자연스러운 상호작용과 실패·침묵·repair가 보존됨 |
| Voice separability and quality | 20 | 음악·변조·overlap이 적고 음색과 delivery를 일관되게 측정 가능 |
| Relationship and context diversity | 15 | 여러 interlocutor, 친밀도와 일상·진지·작업 상황을 포함 |
| Behavioral transition richness | 10 | 농담, 진지함, 불편함, tone shift와 복구가 관찰됨 |
| Temporal consistency and traceability | 10 | 고정 기간 안에서 source date와 timestamp가 추적되고 큰 persona drift가 없음 |

각 dimension은 0부터 5까지 평가한 뒤 `score / 5 * weight`로 환산한다. B evidence만 있는
dimension은 최대 3점으로 제한한다.

판정:

- `advance`: 모든 hard gate 통과, 총점 75 이상, H2~H7에 A evidence
- `hold`: evidence 부족 또는 총점 60~74
- `reject`: hard gate 실패 또는 총점 60 미만

후보 점수 차이가 5점 이하면 순위를 강제로 확정하지 않는다. 동일한 deep survey를 추가한
뒤에도 차이가 유지되지 않을 때만 천우의 선호를 tie-breaker로 사용한다. 선호는 rubric
점수를 수정하지 않고 별도 decision note로 남긴다.

## Fair source sampling

모든 후보는 같은 관찰 예산과 source 구성을 사용한다. 좋은 장면만 골라 비교하지 않도록
baseline sample과 rare-event sample을 분리한다.

### Stage A — screening

- 후보당 source 6개
- 총 관찰 시간 60분
- 서로 다른 interlocutor 최소 2명
- 편집이 적은 연속 대화 source 최소 2개
- 깨끗한 단일 화자 음성 source 최소 1개
- 진지함 또는 tone transition이 포함된 source 최소 1개

일반 행동과 음질 점수는 source마다 사전에 정한 연속 10분 구간으로 평가한다. 영상 시작의
광고·intro를 제외한 첫 연속 대화 구간처럼 재현 가능한 선택 규칙을 candidate card에
기록한다.

### Stage B — finalist deep survey

- `advance` 또는 상위 `hold` 후보만 수행
- 후보당 source 12개, 총 관찰 시간 180분
- 최소 3개의 relationship/context group
- source 기간의 앞·중간·뒤 time band 포함
- baseline 연속 구간과 별도로 tone shift, disagreement, repair 같은 rare event를 표적 관찰

Rare-event sample은 행동 다양성을 확인하는 용도이며, 평상시 빈도를 추정하는 baseline
sample과 섞지 않는다.

## Candidate evidence card

아래 template의 실제 값은 private manifest에 둔다. 공개 decision summary에는
`candidate_id`, 점수, evidence level과 판정 근거만 남긴다.

```text
candidate_id:
private_identity_ref:
survey_version:
source_period_start:
source_period_end:
surveyed_at:

hard_gates:
  H1: pass | hold | reject
  H2: pass | hold | reject
  H3: pass | hold | reject
  H4: pass | hold | reject
  H5: pass | hold | reject
  H6: pass | hold | reject
  H7: pass | hold | reject
  H8: pass | hold | reject
  H9: pass | hold | reject

samples:
  - private_source_ref:
    observed_start:
    observed_end:
    interlocutor_group:
    context_group:
    edit_status:
    audio_quality:
    observed_behavior:
    evidence_level:

scores:
  coupled_scene_availability:
  spontaneity_edit_integrity:
  voice_separability_quality:
  relationship_context_diversity:
  behavioral_transition_richness:
  temporal_consistency_traceability:
  total:

unknowns:
confounds:
decision: advance | hold | reject
decision_reason:
user_tie_break_note:
```

## Confounds to record

- 카메라용 persona 또는 특정 프로그램용 역할
- scripted segment, 광고, 낭독과 연기
- jump cut, 자막 재구성, background music, voice effect
- 한 interlocutor 또는 한 관계에 치우친 source
- 공개 활동 시기에 따른 말투·음색·장비 변화
- 질병, 피로, 술, 공연 같은 일시적인 음성 상태
- 자동 자막 오류와 대화 상대의 발화가 섞인 transcript

이 항목들은 곧바로 후보를 배제하지 않지만, 어느 데이터가 stable reference이고 어느 것이
상황 의존 variation인지 구분할 근거가 된다.

## Approval gate

Stage B 결과가 준비되면 천우에게 다음을 함께 보고한다.

- 익명 후보별 hard gate 결과와 총점
- 각 점수의 A/B evidence 수
- 예상 usable behavior/voice/coupled data 범위
- 주요 confound와 추가 확인 비용
- 추천 후보와 fallback 후보

천우가 한 후보와 source 기간을 명시적으로 승인하기 전에는 영상 다운로드, 외장 corpus
생성 또는 전사를 시작하지 않는다. 실제 target 선택은 별도 decision Issue로 기록한다.
