# Winter V1 Maturity and Enhancement Ledger

- 최초 기록: 2026-08-15
- 목적: 구현된 기능을 완성형으로 오해하지 않고 현재 성숙도, 실제 근거, 실패와 다음
  고도화 gate를 지속적으로 유지한다.

## 성숙도 정의

| Level | 의미 |
| ---: | --- |
| 0 | 설계 또는 아이디어만 있음 |
| 1 | deterministic fake와 offline contract가 있음 |
| 2 | 실제 local vertical slice가 Shadow/실험 경로에서 동작함 |
| 3 | held-out 또는 반복 실제 평가가 있고 실패 범위를 알고 있음 |
| 4 | 명확한 gate를 통과해 제한적으로 운영 상태에 사용함 |
| 5 | 장시간·회귀·실패·rollback 검증을 반복 통과한 성숙 기능 |

한 issue의 acceptance criteria를 만족한 것은 해당 **범위 완료**이지 기능 전체가 Level 5라는
뜻이 아니다.

## 현재 Ledger

| 기능 | Level | 현재 가능한 것과 근거 | 알려진 한계 | 다음 고도화 gate |
| --- | ---: | --- | --- | --- |
| Shared Core / interfaces | 3 | CLI·Web·Voice가 같은 Core와 SQLite 사용, offline 회귀 통과 | 장시간 동시성·복구·30분 자유 대화 부족 | 30분 cross-interface continuity와 장애 복구 |
| Direct Memory / Reflection / Current State | 3, 검토형 | 실제 후보 5개 사용자 처리, topic별 최신 값 교체, `./winter state` Timeline, 변화 질문에만 History 주입·인과 추측 금지 smoke 통과 | 자연어 상태 변경→topic 후보 미연결, 의미 중복, CPU 직렬 지연, Timeline 변경 후 전체 pytest 재실행 보류 | 전체 회귀 재검증, 자연어 current-state 후보 추출 audit와 비교 응답 평가 |
| TurnUnderstanding | 3, Shadow | schema, exact evidence, JSON Schema, 10 scene 평가 | 최종 5/10; preference/project temporal·proposal 누락 | 문서화된 activation 기준 전부 통과 |
| OutcomeEvaluator | 3, Shadow | provenance와 blind review DB v3; 새 locked v2 18/30, synthetic 7/7 gates, 실제 검토 CLI·audit 동작 | corrected/rejected·continuation·ambiguous 세부 필드 실패, 실제 blind review 0/30 | 천우의 실제 Shadow 30+ blind audit 후 candidate-only activation |
| DialogueDirector / review | 3 | 행동 선택, 길이 제한, 일부 회피 응답 repair 테스트 | 선택 품질이 TurnUnderstanding/Outcome으로 학습되지 않음 | 실제 대화 outcome 기반 행동별 성공률 |
| Open Loop | 3 | 명시적 미완료 주제 저장·재시작 callback | 간접 약속·완료 판정·expiry 부족 | Outcome으로 continued/abandoned 검증 |
| Winter Belief | 3 | 근거·confidence·revision lifecycle, active만 prompt 사용 | 자동 reflection·반증 수집 없음 | counterevidence와 사용자 검토 cycle |
| Verbal Style | 3 | Reference 기반 profile과 held-out 문장 길이 평가 | Zeta/Polybuzz 수준의 관계 행동·장기 변화 아님 | 장시간 joint dialogue 선호 평가 |
| Voice identity | 2 | Chatterbox→Seed-VC 수직 경로와 실제 폰 재생 | 자연스러움·질문 억양·사람 착각 수준 미달 | 새 TTS/voice path blind listening gate |
| Phone STT/Web | 2 | push-to-talk→Whisper→Core→SSE audio 동작 | VAD·barge-in·streaming STT·네트워크 복구 부족 | 실제 이동 환경 latency와 interruption 평가 |
| SelfModel / Capability Registry | 0 | V1 설계만 있음 | 실제 가능·불가능을 runtime 근거로 설명 못함 | health/evaluation 기반 read-only registry |
| Improvement Ledger / proposal | 0 | 이 maturity 문서와 V1 설계만 있음 | 대화 신호가 정식 improvement state로 연결되지 않음 | problem→proposal→approval state machine |
| Worker Level 3 | 0 | 승인·격리·검증·채택 정책만 있음 | 실행 adapter와 rollback cycle 없음 | Fake Worker state machine부터 offline 검증 |

## 지속 갱신 규칙

기능을 추가하거나 수정할 때 다음을 같은 작업 안에서 갱신한다.

1. 현재 가능한 범위
2. 검증 명령과 실제 수치
3. 아직 실패하거나 측정하지 않은 범위
4. 운영 상태에 연결할 activation gate
5. 다음 고도화 작업

새 기능은 기본 Level 1 또는 2에서 시작한다. 문서나 단일 성공 사례만으로 Level을 올리지
않으며, 실패 결과도 삭제하지 않고 다음 판단의 baseline으로 남긴다.
