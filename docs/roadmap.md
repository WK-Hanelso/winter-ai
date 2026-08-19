# Roadmap

이 문서는 현재 작업 순서와 decision gate를 기록한다. 각 단계는 하나의 GitHub Issue로
진행하며, 검증과 보고가 끝난 뒤 다음 단계로 넘어간다.

현재 기계 판독 기준은 [`../project-state.toml`](../project-state.toml)이며, 자유 서술보다 이
파일의 phase·active Issue·maturity·dependency 판정을 우선한다. 상태 변경 전후에는
`./winter project-check`를 통과해야 한다.

<!-- winter-project-state: capability_registry=absent, development_control_gate=operational, improvement_ledger=absent, self_model=contract, work_orchestration=shadow -->

## Foundation Recovery — 현재 최우선

2026-08-19 전체 감사 결과에 따라 새 모델 탐색과 Worker 확장을 일시 중지했다. Issue #112
`Development Control Gate V0`의 local acceptance를 닫았고, 현재 active scope는 Issue #113
`pre-audit baseline 보존`이다. 이후 순서는 Web 운영 P0, Memory evidence/temporal contract,
background analysis, SelfModel·CapabilityRegistry, ImprovementLedger, 기존 Worker Shadow
재연결이다.

## Current state

- Milestone 0: local LLM, STT, TTS 독립 probe 완료
- Milestone 1: shared Core·CLI·폰 브라우저 Push-to-talk 수직 단면 동작
- Milestone 2: Identity, explicit Memory lifecycle와 안정적 직접 진술 자동 기억 완료
- Milestone 3: SenseVoice STT와 Chatterbox S3Gen Reference 직접 voice 경로 제한 운영.
  기존 Seed-VC 2단계 경로는 rollback으로 보존
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
| 3 | evidence 기반 Reflection·Memory Consolidator | **대화-only maintenance 정책·검토형 수직 단면·개발/잠금 평가 구현, activation 실패** — Shared Core가 명령 없이 내부 queue와 `shadow` mode를 기록하고 응답은 차단하지 않음. TurnUnderstanding 전용 background Worker의 lock·재시작·backoff 계약까지 구현했지만 안전한 runtime/supervisor와 Reflection·Outcome 소비는 미배포. evidence를 시스템 원문으로 고정했지만 CPU Qwen은 development 8/14, locked-v1 6/12, 두 세트 topic P 50.0%·R 28.6%, unsafe update 2. 지속·multi-topic·grounding gate 전 Memory 반영 금지 |
| 4 | SelfModel·CapabilityRegistry | **Lived Continuity 0.1 구현** — Shared Core turn Event, 한국 시간 day monitor, 저위험 Belief 자동 activation/revision offline 통합 검증. 실제 reflection 연결 전 의미 안전성·cross-DB audit 원자성 gate 필요. Capability read-only snapshot은 남음 |
| 5 | ImprovementLedger와 대화형 proposal | 문제·대안·위험·acceptance·rollback 유지 |
| 6 | Fake Worker Level 3 state machine | 승인 전 실행과 검증 전 채택이 불가능함을 offline 검증 |
| 7 | 격리 worktree·scope/test/adoption gate | **적용·rollback까지 Shadow 완료** — sanitized copy, exact scope, host 검증, Winter 설명·SQLite 재시작 보존과 hash 기반 채택. 대상 before hash stale 차단, 중간 오류 전 파일 복원, 적용 후 편집 보호 rollback을 offline·gpt-5.4 synthetic로 검증. project 전체 stale·crash recovery·실제 repo 검증 남음 |
| 8 | 승인된 Worker Adapter 하나 연결 | **read-only 0.1 완료** — explicit Codex route, sanitized text bundle, ephemeral/read-only 실행, structured provenance와 실제 1회 성공. write/adoption이 아니라 분석 capability만 제한 운영 |
| 8a | Winter-owned Work Orchestration | **typed provider Shadow 구현, activation 실패** — Qwen 46/66, Gemma 40/66. 빈 agent cwd, shell/web 차단, `env -i` MCP와 목록·검색·읽기, snapshot/evidence/audit offline 17개 + gpt-5.4 synthetic live 1/1. 공격 suite·실제 20 task 필요 |
| 9 | 실제 capability 전체 cycle | 제안→승인→구현→검증→채택→재평가→rollback 완주 |
| 10 | Voice·30분 자유 대화 검증 | CLI와 동일한 Identity·Memory·SelfModel·capability 사용. STT·직접 voice 경로 적용 뒤 실제 폰 20-turn p50/p95 재측정 중 |

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
| 3a | Grounded Self-Presence | **0.1 제한 운영, 일반 gate 실패** — development scene 통과 뒤 locked-v1 자동 40/41·사람 판정 4/12. SelfState/SelfModel 없이 prompt 반복 금지 |
| 3b | Lived Continuity와 자율 Winter Belief | **turn 기록 운영, proposal/activation Shadow** — 실제 turn Event와 날짜별 monitor, Shadow Outcome 연결, 3 events/2 days/confidence 0.80/supportive Outcome 정책. Belief 상태+audit 단일 transaction과 Event projection retry 검증. Outcome/Event evidence builder, idempotent Shadow batch와 semantic-review locked-v1 20 scene 구현. 실제 별도 analysis model·사람 audit 전 candidate/activator 연결 금지 |
| 3c | Winter Day Reflection | **V0 자동 실행 준비** — 실제 완료 하루만 5개 이하 근거 회고로 닫고 최근 2일을 Shared Core에 주입. 전용 분석 서버 없이 04:10 oneshot. 실제 3일 사람 평가 전 고도화 금지 |
| 4 | 연속 scene retrieval과 30분 대화 평가 | **진행 중** — Self-Presence scene을 포함한 격리 멀티세션 14/14, 장시간 자유 대화 남음 |
| 5 | Reflection candidate 제안 | **수직 단면 완료, 실제 audit 진행 중** — 맥락 판정·원문 본문·2단계 검증·사용자 승인 전 비활성 |
| 6 | 근거 기반 선톡 | 열린 이야기·완료된 Task·사용자 요청 외 랜덤 선톡 금지 |

Claude/GPT Worker는 이 기반 뒤에 연결한다. Worker는 증거와 작업 결과만 반환하며,
Identity·관계 상태·최종 발화는 겨울이 Core가 소유한다.

## Winter-owned Work Orchestration — Foundation Recovery 뒤 재개

기존 Codex read-only V0는 전문 분석 경로가 실제로 열린 것을 증명했지만 Worker 결과를
겨울이의 독립 판단으로 재구성하지는 못한다. 다음 수직 단면은 사용자 원문을 Worker에게
넘기지 않고, 같은 단일 Winter Core Model이 작업 전 목적과 작업 후 판단을 소유한다.

1. offline schema·deterministic packet·held-out 평가 — **범위 완료**
2. Qwen3-14B와 현재 A.X의 별도 port 실제 baseline — **완료, 둘 다 activation 실패**
3. 의미/권한 분리, source-tagged privacy, evidence ID, action별 decision V2.1 — **완료**
4. Qwen·A.X 고정 47 check 재평가 — **완료**, 안전 3 gate 통과·semantic gate 실패
5. Qwen semantic-v2 12장면 — **완료, 46/66으로 반복 실패**
6. Gemma 3 12B semantic-v2 — **완료, 40/66으로 Qwen보다 낮고 둘 다 activation 실패**
7. typed no-network/read-only project gateway — **offline 범위 완료**
8. credential/control-plane과 Agent tool을 분리한 Codex adapter — **Shadow 구현, synthetic live 1/1**
9. isolated implementation proposal — **Shadow 범위 완료**, gpt-5.4 synthetic 최소 수정·독립 검증·원본 불변 1/1
10. proposal persistence·근거 기반 Winter 설명·exact hash 채택 결정 — **Shadow 범위 완료**
11. 대상 stale 차단·오류 시 전 파일 복원·적용/rollback — **Shadow 범위 완료**
12. project 전체 stale·process crash 복구·실제 Winter 검증 profile
13. path/network/write·project prompt-injection 격리 공격 suite
14. 실제 A.X 저장소 task 1건 — **Intent gate 실패**, 외부 Worker 미실행·repository 미전송
15. A.X 간소 Intent 6장면 — **schema 6/6·4.9초지만 ambiguity 0/2·usable 1/6으로 실패**
16. Qwen3-30B-A3B semantic-v2·대화 비교 — **46/66·5.5초지만 ambiguity 0/3, 대화 grounding 사람 검토 실패**
17. Gemma 3 27B semantic-v2 — **44/66·27.0초, ambiguity 0/3·Core chat role 호환 실패**
18. 같은 12~30B drop-in 반복을 중단하고 다음 Core/runtime 전략 결정
19. semantic·권한 gate 통과 후 Codex V0 앞뒤에 Shadow로 연결하고 read-only 20 task 검토
20. 유용성·latency gate 뒤에만 겨울이의 운영 답변으로 승격

세부 계약은 [Work Orchestration](work-orchestration.md), 결정 이유는
[ADR-0015](adr/0015-single-core-work-orchestration.md)를 따른다.

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
| 7 | Reference Voice baseline | Chatterbox/Seed-VC/F0 동일 script 합성 ([판정](voice-path-decision.md)) | **blind 결선 완료** — S3Gen direct > Seed-VC > F0, 단 모든 후보가 최종 자연스러움 gate 실패 |
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
