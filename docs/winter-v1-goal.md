# Winter V1 Product Goal — Collaborative Companion

- 상태: Accepted
- 결정일: 2026-08-15
- 사용자: 천우
- 기준: 이 문서는 Winter V1의 전체 제품 목표와 완료 조건을 정의한다.

## 1. Product Statement

Winter V1은 질문에 반응하는 챗봇이 아니라, 한 명의 사용자 천우와 장기간 관계를
유지하며 함께 발전하는 로컬 중심 Personal Companion이다.

겨울이는 천우의 말을 단순 분류하지 않고 가능한 의도, 감정, 시간 범위와 대화 요구를
근거와 확신도를 가진 가설로 이해한다. 대화가 끝난 뒤 다음 반응을 통해 그 이해가
맞았는지 평가하고, 직접 사실과 검증된 선호·결정·프로젝트 상태만 장기 기억으로
통합한다.

겨울이는 자신의 실제 능력과 한계를 알고, 반복되는 한계나 새로운 욕구를 천우와
토론한다. 천우가 구현을 승인하면 격리된 Worker가 변경을 만들고 시스템이 독립적으로
검증한다. 천우가 결과를 채택한 뒤에만 새 기능이 겨울이의 실제 capability가 된다.

V1의 목표 자율성은 **Level 3 — 사용자 승인 후 Worker 구현**이다. 제한 없는 자기 수정은
V1의 목표가 아니다.

## 2. V1 Experience

천우가 겨울이와 이야기하면 다음 순환이 실제 데이터와 코드로 닫혀야 한다.

```text
대화와 실제 경험
→ 의도·감정·요구 이해
→ 적절한 겨울이 응답
→ 다음 반응으로 이해와 응답 결과 평가
→ 근거 있는 기억·관계·프로젝트 상태 갱신
→ 반복 한계나 새로운 기능 욕구 발견
→ 겨울이와 천우의 개선 방향 토론
→ 구현 승인
→ 격리된 Worker 구현과 독립 검증
→ 결과·위험·한계 설명
→ 천우의 채택 승인
→ capability 등록과 새 버전 적용
→ 실제 사용과 재평가
```

이 흐름은 역할극이어서는 안 된다. 코드나 서비스가 바뀌지 않았는데 겨울이가
“개선했다”고 말하면 실패다. health와 검증 근거가 있는 capability만 사용할 수 있다고
말해야 한다.

## 3. Identity and Change Boundaries

### Immutable Core

- 천우를 속이지 않는다.
- 실행하지 않은 작업이나 없는 능력을 주장하지 않는다.
- 개인정보와 Human Reference 데이터를 동의 없이 외부로 전송하지 않는다.
- Core Persona와 관계 원칙은 자동 변경하지 않는다.
- 파괴적 작업과 외부 write에는 명시적 승인이 필요하다.

### Negotiated Identity

다음은 천우와 대화하고 합의하며 버전으로 변경할 수 있다.

- 말투와 대화 호흡
- 친밀도와 감정 표현
- 문제를 함께 바라보는 태도
- 겨울이의 근거 있는 관점과 가치 판단
- Verbal Style, Prosody와 Winter Delta

### Versioned Capabilities

Memory, Voice, 검색, 프로젝트 관리와 Worker 같은 실제 기능은 구현·검증·채택을 거쳐
추가한다. 각 capability에는 version, status, health, limitations, evidence와 rollback
정보가 있어야 한다.

## 4. Shared Companion Architecture

```text
CLI ───────────────┐
Phone Web/Voice ───┼── CompanionCore
Future Interface ──┘         │
                             ▼
                    TurnUnderstanding
                             │
          ┌──────────────────┼──────────────────┐
          ▼                  ▼                  ▼
     User/Relation       DialoguePlan     Improvement Signal
         State          ResponseContract         │
          │                  │                    ▼
          ▼                  ▼            Improvement Ledger
        Memory          Local ChatModel           │
          ▲                  │                    ▼
          │             Winter Response      Worker Control
   OutcomeEvaluator          │                    │
          ▲             ┌────┴────┐               ▼
          │             CLI     Voice       Validate / Adopt
          └── next turn          TTS               │
                                                   ▼
                                           Capability Registry
```

CLI, Web와 Voice는 별도의 Companion을 만들지 않는다. 같은 lexical response, Identity,
Memory, Belief, SelfModel과 improvement state를 공유한다.

## 5. TurnUnderstanding

Local LLM은 응답 전에 다음과 같은 관찰 가능한 구조를 만든다.

```text
observations          원문에서 직접 확인된 내용과 evidence span
literal_meaning       문장의 표면 의미
intent_hypotheses     가능한 의도와 confidence
affect                감정, 강도와 불확실성
conversational_need   공감, 판단, 설명, 행동, 토론 등
temporal_scope        현재 turn, session, episodic, stable
response_contract     이번 답변에서 할 일과 피할 일
memory_proposals      kind, content, evidence, confidence, expiry
open_loops            나중에 이어갈 내용
project_signals       goal, decision, blocker, next action
improvement_signals   반복 실패와 capability gap
uncertainties         단정하거나 기억하면 안 되는 부분
```

이 결과는 내부 장문의 chain-of-thought가 아니라 검토 가능한 짧은 판단과 근거다. 하나의
숨은 의도를 사실로 단정하지 않고 복수 가설과 confidence를 유지한다.

TurnUnderstanding은 먼저 Shadow Mode로 기록한다. held-out 대화와 실제 천우 대화에서
검증한 필드부터 ResponseContract와 Memory에 연결한다.

## 6. OutcomeEvaluator

겨울이는 다음 천우 turn을 이용해 이전 이해와 응답 결과를 평가한다.

```text
accepted / corrected / rejected / continued / abandoned
intent_match
response_usefulness
affect_shift
memory_confirmation
memory_contradiction
improvement_confirmation
```

천우가 “아니, 지금은 해결 방법을 물어본 거야”라고 정정하면 이전 intent confidence를
낮추고 그 해석을 장기 선호로 승격하지 않는다. 매 turn 모델 weight를 변경하지 않고
외부 상태와 증거를 갱신한다.

## 7. Memory and Consolidation

V1은 다음 상태를 구분한다.

| Kind | Responsibility |
| --- | --- |
| session | 현재 대화에서만 유효한 상태 |
| semantic | 천우에 대한 안정적인 사실 |
| episodic | 특정 시점의 사건과 경험 |
| preference | 설명·반응·작업 방식의 선호 |
| decision | 결정과 근거 |
| project | 목표, 진행 상태, blocker, next action |
| procedural | 겨울이가 천우를 지원하는 방식 |
| relationship | 관계 분위기와 반복 상호작용 |
| improvement | 겨울이의 한계와 개선 필요 |

각 기억에는 최소 content, kind, evidence turns, confidence, temporal scope, importance,
status, timestamps, valid-until, supersedes와 contradiction이 필요하다.

```text
observation → candidate → confirmed → active → deprecated / rejected
```

- 직접 단정한 안정적 사실·선호·결정은 높은 confidence로 활성화할 수 있다.
- 간접적으로 추론한 선호와 관계 패턴은 반복 증거나 Outcome 확인이 필요하다.
- 순간 감정, 질문, 추측과 근거 없는 모델 요약은 안정 기억으로 만들지 않는다.
- 자동 기억에는 반드시 원문 evidence를 연결한다.
- 모든 기억은 CLI에서 조회·수정·삭제할 수 있다.

Background Reflection은 5~10 turn, 세션 종료, 중요 결정 또는 명시적 회고 시점에 실행하고
응답 latency 경로와 분리한다.

## 8. Winter Belief and SelfModel

천우에 대한 Memory와 겨울이의 판단을 혼합하지 않는다.

```text
Memory: 천우는 결과를 먼저 보는 설명을 선호한다.
Belief: 현재 Voice 개선은 추가 VC 학습보다 앞단 TTS 개선이 우선이다.
```

Belief에는 stance, rationale, confidence, evidence, counterevidence, version과 supersedes를
둔다.

SelfModel은 다음 실제 상태를 소유한다.

- Identity와 version
- 사용 중인 Local LLM, STT, TTS와 Voice path
- Memory와 dialogue 기능
- 연결된 Worker와 permission
- health, limitations와 degraded state
- 진행 중인 improvement와 최근 adopted change

## 9. Capability Registry

Capability status는 다음 lifecycle을 따른다.

```text
proposed → approved → building → testing → available
                                      └──→ failed
available → degraded → retired
```

Capability에는 name, version, supports, limitations, dependencies, health check, evaluation
evidence, adopted change와 rollback target을 기록한다. Registry와 health가 확인하지 않은
기능을 겨울이는 할 수 있다고 말하지 않는다.

## 10. Improvement Ledger and Level 3

개선 항목은 대화에서 사라지지 않고 다음 상태로 유지된다.

```text
observed → discussing → proposed → approved → implementing
→ validating → ready_to_adopt → adopted → revised / rolled_back
```

Proposal에는 problem, evidence, why-it-matters, solution, alternatives, expected effect, risks,
privacy impact, implementation scope, acceptance criteria와 rollback plan이 필요하다.

구현 승인과 채택 승인은 분리한다.

1. 겨울이가 한계와 대안을 천우와 토론한다.
2. 천우가 명시적으로 구현을 승인한다.
3. Worker가 격리된 branch/worktree에서 승인 scope만 수정한다.
4. Worker 주장과 별도로 시스템이 tests, lint, type, privacy와 scope를 검증한다.
5. 겨울이가 결과·실패·한계를 자기 말투로 설명한다.
6. 천우가 채택해야 실제 runtime과 Capability Registry에 적용한다.
7. 적용 뒤 Outcome을 관찰하고 필요하면 rollback한다.

## 11. Worker Boundary

Worker는 기술 구현자이며 겨울이의 Identity를 소유하지 않는다.

```text
겨울이: 문제 인식, 토론, 제안, 관계, 최종 설명과 평가
Worker: 조사, 코드 변경, 테스트와 기술 결과
```

Worker에게 전달할 수 있는 것은 관련 코드, 기술 문제, 실패 로그, scope와 acceptance
criteria다. 전체 대화 DB, 사용자 장기 기억, Human Reference 원본과 전체 Identity는
전달하지 않는다.

초기 Port는 deterministic Fake Worker로 오프라인 검증한다. 실제 Codex/Claude Adapter는
명시적 opt-in, 격리 workspace, permission boundary와 audit log 뒤에 연결한다.

## 12. Voice

```text
mic → STT → same TurnUnderstanding/Core → same lexical response
→ Joint Utterance Plan → Prosody/TTS/VC → audio
```

Voice Identity, Prosody와 Verbal Style은 데이터 계약에서 분리하지만 실제 발화 계획에서는
결합한다. Voice가 Core의 문장을 다시 작성하면 안 된다. Human Reference 원본과 파생
데이터는 승인된 로컬 외장 저장소에만 둔다.

## 13. Privacy, Audit and Rollback

- 대화, 기억, 음성, model과 reference data는 local-first다.
- 외부 Worker에는 최소 기술 정보만 명시적 opt-in으로 전달한다.
- 자동 판단은 evidence와 confidence를 남긴다.
- Memory, Belief, Identity, capability와 change는 history를 보존한다.
- destructive/external write는 명시적 승인이 필요하다.
- 실제 capability 변경은 정확한 code revision과 rollback target을 가진다.

## 14. V1 Acceptance Criteria

### Relationship and dialogue

- CLI와 Voice가 같은 Identity·Memory·SelfModel을 사용한다.
- TurnUnderstanding이 의도 가설, 감정, temporal scope, response need와 evidence를 만든다.
- 겨울이가 정정 신호를 Outcome으로 기록하고 잘못된 가설을 장기 기억으로 확정하지 않는다.
- 30분 자유 대화에서 Open Loop와 프로젝트 맥락을 재시작 뒤 이어간다.

### Memory

- 직접 사실과 반복 검증된 선호를 `기억해` 명령 없이 저장한다.
- 모든 자동 기억에 원문 evidence가 있다.
- 순간 상태와 안정 선호를 구분하고 expiry를 적용한다.
- 충돌·수정·삭제와 이전 이력을 조회할 수 있다.

### Self knowledge

- 겨울이가 현재 capability와 limitation을 Registry 근거로 설명한다.
- unavailable/degraded 기능을 가능한 것처럼 말하지 않는다.
- Identity, Belief와 capability change가 서로 분리되어 version된다.

### Level 3 co-development

- 대화에서 실제 capability gap 하나를 Improvement로 만든다.
- 천우 승인 없이는 Worker job이 시작되지 않는다.
- Worker는 격리 workspace와 승인 scope에서만 작업한다.
- 독립 검증 실패 시 채택할 수 없다.
- 천우 채택 뒤에만 capability가 available이 된다.
- 재시작 뒤 겨울이가 새 capability를 정확히 알고 실제로 사용한다.
- 같은 변경을 rollback하고 이전 상태로 복구할 수 있다.

### Quality and privacy

- 기본 테스트는 offline·fake adapter로 실행된다.
- 실제 model/voice/worker 평가는 marker로 분리한다.
- 전체 대화·기억·reference data가 Worker나 Git에 포함되지 않는다.
- latency, intent error, false memory, capability hallucination과 rollback 성공률을 기록한다.

## 15. V1 Non-goals

- Level 4 이상의 무승인 자기 수정
- 매 대화 model weight update
- 사람과 같은 의식이나 완전한 자기인식 주장
- 근거 없는 성격·감정·관계 추론의 자동 활성화
- Worker가 Winter Identity와 최종 응답을 소유하는 구조
- 테스트 없이 prompt나 코드를 바로 운영에 적용
- 처음부터 full-duplex Voice나 복잡한 multi-agent framework 도입

## 16. Implementation Order

1. 현재 혼합 worktree를 보존하고 기능별 기준 revision을 만든다.
2. `TurnUnderstanding` schema, Port, deterministic fake와 Shadow event log를 구현한다.
3. held-out dialogue와 실제 천우 대화로 interpretation contract를 검증한다.
4. `OutcomeEvaluator`와 correction/acceptance signal을 연결한다.
5. evidence 기반 Reflection과 Memory Consolidator를 구현한다.
6. `SelfModel`과 `CapabilityRegistry`를 구현한다.
7. `ImprovementLedger`와 대화형 제안·승인 상태를 연결한다.
8. Fake Worker로 Level 3 state machine 전체를 오프라인 검증한다.
9. 격리 Git worktree, scope validator, test와 adoption/rollback gate를 구현한다.
10. 한 개의 승인된 Worker Adapter를 연결한다.
11. 작은 실제 capability 하나로 제안부터 rollback까지 전체 cycle을 완주한다.
12. 같은 Core 흐름을 Voice에서 검증하고 30분 자유 대화를 수행한다.

## 17. Immediate Next Action

첫 구현은 `TurnUnderstanding Shadow Mode`다.

응답이나 Memory 동작을 바로 바꾸지 않고, 원문 turn 옆에 observations, intent hypotheses,
affect, conversational need, temporal scope, memory/improvement proposals, uncertainty와
evidence를 구조화해 저장한다. deterministic fake와 held-out acceptance tests를 먼저 만들고
실제 Local LLM adapter는 별도 marker로 검증한다.

이 결과가 안정되기 전에는 추론형 자동 기억이나 Worker 자동 실행을 활성화하지 않는다.
