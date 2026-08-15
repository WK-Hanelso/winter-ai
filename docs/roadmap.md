# Roadmap

이 문서는 현재 작업 순서와 decision gate를 기록한다. 각 단계는 하나의 GitHub Issue로
진행하며, 검증과 보고가 끝난 뒤 다음 단계로 넘어간다.

## Current state

- Milestone 0: local LLM, STT, TTS 독립 probe 완료
- Milestone 1: shared Core·CLI·폰 브라우저 Push-to-talk 수직 단면 동작
- Milestone 2: Identity, explicit Memory lifecycle와 안정적 직접 진술 자동 기억 완료
- Milestone 3: Chatterbox V3 → Seed-VC의 상주 voice 경로 동작, F0 대체 경로 결선 평가 중
- Milestone 4: Human Reference 선정·외장 storage·수집·화자 판정·말투 baseline과
  89.4분 voice 학습 완료. 추가 학습은 중단하고 voice architecture decision gate 진행 중

Winter V1의 전체 제품 목표는 [Winter V1 Product Goal](winter-v1-goal.md)로 확정했다.
목표 자율성은 Level 3이며, 천우 승인 전 Worker 실행과 검증·채택 전 capability 등록은
허용하지 않는다.

## V1 Collaborative Evolution — implementation order

| Order | Work item | Gate |
| ---: | --- | --- |
| 1 | TurnUnderstanding schema와 Shadow event log | **비개입 구현·baseline 완료, activation 실패** — 5/10 scene. Memory/Response 연결 금지 |
| 2 | OutcomeEvaluator | **blind review 경로 완료, Shadow 유지** — locked v2 18/30, 7/7 gates, 실제 review 0/30. 30+ audit 전 연결 금지 |
| 3 | evidence 기반 Reflection·Memory Consolidator | **검토형 수직 단면 구현** — 원문 230개 보존·사용자 발화 126개 연결, 원문 근거 후보만 생성. 실제 audit·중복 병합 전 자동 활성화 금지 |
| 4 | SelfModel·CapabilityRegistry | health/evaluation 없는 능력 주장 금지 |
| 5 | ImprovementLedger와 대화형 proposal | 문제·대안·위험·acceptance·rollback 유지 |
| 6 | Fake Worker Level 3 state machine | 승인 전 실행과 검증 전 채택이 불가능함을 offline 검증 |
| 7 | 격리 worktree·scope/test/adoption gate | 현재 runtime과 사용자 데이터를 Worker에서 분리 |
| 8 | 승인된 Worker Adapter 하나 연결 | 기술 정보만 최소 전달하고 audit 기록 |
| 9 | 실제 capability 전체 cycle | 제안→승인→구현→검증→채택→재평가→rollback 완주 |
| 10 | Voice·30분 자유 대화 검증 | CLI와 동일한 Identity·Memory·SelfModel·capability 사용 |

**Order 1 TurnUnderstanding Shadow Mode**의 비개입 수직 단면과 첫 held-out baseline은
완료했다. 현재 5/10 scene으로 activation gate는 실패했다. 실제 대화에서 hypothesis
precision과 false-memory proposal을 계속 평가하며, gate가 통과하기 전에는 추론형 자동
기억을 active로 만들거나 실제 Worker 자동 실행을 연결하지 않는다.

## Companion Experience Foundation — 2026-08-15 재정렬

Voice 품질만으로 Zeta·PolyBuzz 계열의 관계형 경험이 생기지 않는다는 사용자 판정에 따라,
Worker Integration 전에 겨울이가 대화를 이어가고 자기 관점을 지속하는 기반을 만든다.

| Order | Work item | Gate |
| ---: | --- | --- |
| 1 | 근거·신뢰도·변경 이력을 가진 Winter Belief | **완료** — active 관점만 사용, 동일 주제 충돌 방지 |
| 2 | 열린 이야기와 후속 시점 저장 | **완료** — verbatim Open Loop와 재시작 callback 검증 |
| 3 | DialogueDirector와 대화 행동 선택 | **완료** — 행동 metadata·실패 review·1회 repair |
| 4 | 연속 scene retrieval과 30분 대화 평가 | **진행 중** — 자동 기억 포함 멀티세션 11/11, 장시간 자유 대화 남음 |
| 5 | Reflection candidate 제안 | **수직 단면 완료, 실제 audit 진행 중** — 맥락 판정·원문 본문·2단계 검증·사용자 승인 전 비활성 |
| 6 | 근거 기반 선톡 | 열린 이야기·완료된 Task·사용자 요청 외 랜덤 선톡 금지 |

Claude/GPT Worker는 이 기반 뒤에 연결한다. Worker는 증거와 작업 결과만 반환하며,
Identity·관계 상태·최종 발화는 겨울이 Core가 소유한다.

## Milestone 4 — Human Reference Baseline

Epic: [#66](https://github.com/WK-Hanelso/winter-ai/issues/66)

| Order | Work item | Output | Gate |
| ---: | --- | --- | --- |
| 1 | [설계 결정과 문서 기준선](https://github.com/WK-Hanelso/winter-ai/issues/67) | ADR, architecture, roadmap | 완료 |
| 2 | [Reference 선정 기준](https://github.com/WK-Hanelso/winter-ai/issues/69) | hard gate, rubric, evidence template | 완료 |
| 3 | [외장 저장소와 manifest](https://github.com/WK-Hanelso/winter-ai/issues/71) | 재현 가능한 storage contract | 완료 |
| 4 | [Multimodal schema](https://github.com/WK-Hanelso/winter-ai/issues/73) | scene alignment와 annotation schema | 완료 |
| 5 | [소규모 수집·정렬 probe](https://github.com/WK-Hanelso/winter-ai/issues/75) | 승인된 외장 storage의 실제 source와 usable subset | 완료. private media는 Git 제외 |
| 5a | [source metadata preflight](https://github.com/WK-Hanelso/winter-ai/issues/77) | 다운로드 없는 접근성·duration·format 확인 ([문서](reference-source-probe.md)) | cap 초과 또는 접근 실패 시 실제 수집 진행 거부 |
| 5b | [자막 품질 측정](https://github.com/WK-Hanelso/winter-ai/issues/82) | 자막의 `raw_transcript` 사용 가능성 판정 ([문서](reference-subtitle-quality.md)) | 편집 강도 측정 후 전사 방식 결정 |
| 5c | [오디오 파일럿 수집](https://github.com/WK-Hanelso/winter-ai/issues/84) | source-003 오디오 확보 ([문서](reference-audio-ingest.md)) | 파일럿 성공 후 나머지 source 수집 여부 결정 |
| 5d | [STT 파일럿](https://github.com/WK-Hanelso/winter-ai/issues/86) | 구간 전사 품질 측정 ([문서](reference-transcription-probe.md)) | 전사 방식 확정 전 diarization 설계 필요 |
| 5e | [Voice Set 추가](https://github.com/WK-Hanelso/winter-ai/issues/88) | 단독 화자 source 확보와 STT 재측정 ([ADR](adr/0003-reference-transcription-strategy.md)) | 화자 분리 설계 후 전체 전사 진행 |
| 5f | [말투 특성 추출](https://github.com/WK-Hanelso/winter-ai/issues/90) | 단독 화자 전사의 말투 통계 ([문서](reference-speech-style.md)) | VerbalStylePlanner 교체 설계의 입력 |
| 8a | [말투 profile 적용](https://github.com/WK-Hanelso/winter-ai/issues/92) | Reference 근거 VerbalStyle profile ([문서](verbal-style-profiles.md)) | held-out 평가 전 baseline 확보 |
| 8b | [grounding 정책](https://github.com/WK-Hanelso/winter-ai/issues/94) | 겪지 않은 경험·없는 기억 진술 억제 ([문서](grounding-policy.md)) | 프롬프트 제약은 보장이 아님을 전제 |
| 8c | [말투 held-out 평가](https://github.com/WK-Hanelso/winter-ai/issues/96) | 스타일 거리와 바닥값 ([문서](style-holdout-evaluation.md)) | Order 10 학습 결정의 근거 |
| 8d | [측정 신뢰구간·내용 평가](https://github.com/WK-Hanelso/winter-ai/issues/106) | 반복·신뢰구간, 쌍 42개, 응답 음성 42개 ([문서](content-holdout-evaluation.md), [음성](response-audio-clips.md)) | 말투는 바닥값 도달, 내용은 우연 대비 0.157 |
| 6 | 행동·분위기 annotation | held-out 가능한 behavior labels | inter-annotation/수동 검토 기록 |
| 6a | [화자 분리](https://github.com/WK-Hanelso/winter-ai/issues/98) | Sortformer + 화자 판정 + 단어 정렬로 쌍 데이터 생성 ([문서](diarization.md)) | 5분당 11쌍. 확장 규모 추정 가능 |
| 7 | Reference Voice baseline | Chatterbox/Seed-VC/F0 동일 script 합성 ([판정](voice-path-decision.md)) | 객관 지표 완료, 천우 blind 청취 대기 |
| 8 | CLI behavior baseline | prompt/few-shot Reference 응답 | held-out behavior score 기록 |
| 9 | Voice joint baseline | shared text + Reference delivery | text/voice/joint gap 분리 |
| 10 | [학습 방식 결정](https://github.com/WK-Hanelso/winter-ai/issues/103) | [ADR-0004](adr/0004-training-decision.md) | **D1 성공(말투 학습 불필요). D2는 측정 개선 후 재판정** |
| 11 | 수직 단면 검증 | 재현 명령·결과·한계 | Milestone 완료 보고 |

각 작은 Issue가 끝날 때 무엇 때문에 무엇을 했고, 그 결과 이제 무엇이 가능한지 사용자에게
보고한다. 데이터가 부족하거나 결과가 나쁘다는 이유만으로 다음 학습 단계를 자동 승인하지
않는다.

Order 5의 실제 source 접근 전에는 사용자가 Reference Human, source 목록과 전용 외장
storage 절대경로를 승인해야 한다. 실제 identity와 URL은 외장 private manifest에만 두며,
승인이나 mount 확인이 없으면 다운로드·storage write·synthetic fallback을 수행하지 않는다.

## Milestone 5 — Winter Character Foundation

검증된 Reference Human과의 `Winter Delta`를 정의한다. 말투, 음성, 관계 행동을 함께
변형하고 CLI와 Voice에서 독립적인 겨울이 OC로 인식되는지 평가한다.

## Milestone 6 — Continual Preference Update

겨울이의 Core Persona와 천우에게 적응하는 preference를 분리한다. 반복 관찰, 명시적
선호와 변경 이력을 통해 안정적으로 업데이트한다.

## Milestone 7 — Worker Integration

겨울이의 Identity를 외부 Worker에 넘기지 않고 전문 작업 결과만 위임한다. 최종 응답은
겨울이가 shared character policy로 재구성한다.
