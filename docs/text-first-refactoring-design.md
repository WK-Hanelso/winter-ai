# Text-first Winter — 모듈 설계 초안

- 작성일: 2026-09-10
- 상태: T0 #128의 전환 근거로 채택된 설계 초안. 세부 구현·운영 승격 또는 모델 채택 기록이 아니다.
- 사용자 결정: 텍스트에서 정체성·기억의 연속성, 외부 Agent 활용, 관계가 느껴지는 대화를
  먼저 확보한다. Voice 고도화와 검증은 후순위로 옮긴다.
- 확인 baseline: `0c4f557`, `feat/126-winter-owned-work-canary`.
- 변경 범위: 설계 초안과 작업 계획 문서. 실행 코드·DB·서비스·기존 품질 gate는 변경하지 않았다.
- 순서별 작업 목록: [Text-first 구현 TODO](text-first-implementation-todo.md).

## 1. 제품 범위와 전환

2026-09-11 보완: [ADR 0039](adr/0039-dialogue-capability-first.md)의 대화 능력 확보 전략과
형체 없는 표현 경계를 따른다. DialogueEngine의 모델/예시 선택을 핵심 작업으로 올리며,
검사 규칙만으로 대화 품질을 보충하지 않는다. 기존 Response/Prosody 계약을 재사용하고
선택적 한숨·웃음은 이후 delivery로 분리한다. 이번 결정은 새로운 Voice 구현이 아니다.

첫 목표는 천우가 CLI에서 계속 같은 겨울이와 이야기하고, 이전 사건을 필요한 순간에
회상하며, 외부 Agent에 맡긴 작업이 끝나도 그 대화가 이어지는 경험이다.

Voice와 Human Reference 원본/정렬 데이터는 보존한다. Voice 학습·배포·실기 검증은 이번
텍스트 목표의 선행조건이 아니다. 이것은 사용자의 최신 우선순위 변경이다. 실행 중인 음성
서비스의 실제 중지·삭제를 이 문서 작성으로 수행하지 않는다.

Level 3 자기 코드 개선은 이후 확장으로 보존한다. 기존 read-only Agent 실행을 사용할 수
있게 만드는 데 전체 source adoption lifecycle 완성을 먼저 요구하지 않는다. write는 기존
구현 승인·격리·독립 검증·별도 채택 승인 경계를 유지한다.

T0 #128에서 `project-state.toml`을 `text_first_foundation`으로 전환한다. Epic은 #127,
첫 코드 작업은 #129다. 이 설계는 새 기능 activation 근거가 아니며,
AGENTS/제품 목표/README/Roadmap/Maturity와 함께 관리한다. #126의 실패와
미완료 phone gate는 역사로 보존하며 성공으로 닫지 않는다. 제안한 기능을 operational로
올리지 않고, 검증된 기존 기능도 Voice 우선순위 변경만으로 absent로 낮추지 않는다.

## 2. 현재 코드에서 확인한 경계 문제

| 확인 위치 | 현재 동작 | 변경 방향 |
| --- | --- | --- |
| `src/companion/core.py` `_prepare` | 기억 저장·조회·행동 선택·prompt 조립·분석 enqueue·작업 라우팅을 함께 수행 | 독립 서비스로 점진 추출, Core는 호출 순서 조정 |
| `src/companion/cli/__main__.py` `run` | DB 생성·모델·Worker 조립과 관리 명령/사용자 대화가 함께 있음 | 공용 bootstrap과 text session loop 분리 |
| `src/companion/context.py` | 최근 메시지에만 메시지 수/문자 수 예산 적용 | system context·검색 결과·출력 여유까지 하나의 입력 예산으로 관리 |
| `core.py`, `conversation_recall.py` | 전체 대화를 읽고 오래된 대화를 어휘 겹침으로 선택 | bounded recent read와 검색 index, 선택된 원문만 조회 |
| `src/companion/memory.py` | 전체 active memory 순회, current state에 높은 점수 부여 | 관련도·시간·정정·근거를 함께 다루는 검색 서비스 |
| `dialogue_act.py`, `dialogue_director.py` | 현재 문장의 regex가 distress/설명/의견 등의 행동을 선결정 | 정책 선택기를 교체 가능한 Port로 감싸고 맥락형 후보와 비교 |
| `src/companion/verbal_style.py` | 일부 profile에서 존댓말 비율을 turn별 확률로 선택 | 분포 재현 실험을 보존하되 관계/상황 적합성으로 후보를 평가 |
| `src/companion/core.py` `_finish` | 응답 자르기·claim 검사·여러 저장소 기록 | 최종 발화 확정과 기록 경계를 명시 |
| `owned_work.py`, `owned_work_repository.py` | background 작업·결과 inbox·재시작 복구 존재 | text interface의 접수·완료 알림에 재사용 |

이 문서의 관찰은 정적 코드 확인이다. 실제 장기 대화 성능이나 새 모델 품질 측정이 아니다.
기존 TurnUnderstanding/Outcome의 실패 gate는 그대로 유지한다.

## 3. 목표 모듈 구조

Python monorepo 안의 모듈 경계다. 모듈마다 서버나 LLM을 만들지 않는다.

```text
TextInterface ── CompanionCore (turn/session orchestration)
                      │
                      ├─ CharacterState: identity + 관계 상태 + Winter 관점
                      ├─ MemoryService: 원문/기억/열린 이야기의 선택적 회상
                      ├─ DialogueEngine: 문맥 조립 + Local ChatModel + 응답 검사
                      └─ TaskService: 접수/상태/결과 + 기존 Worker adapters
                      │
                 Turn 저장 + durable outbox
                      ↓
                 MemoryMaintenance (background)
                 ├─ 사건/기억 후보 추출 및 index 갱신
                 └─ 근거·시간·충돌 검증 뒤 허용된 상태만 반영
```

| 모듈 | 입력 → 출력 계약 초안 | 재사용 |
| --- | --- | --- |
| CharacterState | session/time → CharacterSnapshot | `identity.py`, `beliefs.py`, `capabilities.py`, `open_loops.py` |
| MemoryService | RecallRequest → EvidenceBundle | SQLite conversation/memory, recall, day reflection |
| DialogueEngine | TurnContext → ReplyDraft → CompanionResponse | `ChatModel`, planner, reviewer, claim gate |
| TaskService | 승인 범위가 정해진 TaskRequest → receipt/status/result | owned work, typed gateway, Codex adapters, inbox |
| MemoryMaintenance | 저장된 turn ID → 처리 checkpoint/후보/변경 이력 | maintenance queue와 기존 Shadow 분석기 |

### CharacterSnapshot

- 승인된 `identity_version`과 안정적 정체성
- 근거가 있는 관계별 대화 선호와 그 provenance
- 검증된 Winter Belief와 수정 이력
- 관련된 열린 이야기와 실제 능력/한계

관계는 하나의 친밀도 숫자로 대체하지 않는다. 호칭·존대·농담 허용·경계 같은 직접 합의와
최근 대화의 불확실한 분위기를 구분한다. 상태마다 근거·version·유효 범위를 둔다. 친구,
연인, 가족 중 특정 관계를 이번 초안으로 자동 채택하지 않는다.

### TurnContext

현재 발화, bounded recent turns, CharacterSnapshot, EvidenceBundle, 관련 작업 상태,
입력/출력 token budget을 함께 전달한다. 분석기의 추측은 사실 context와 구분하며, 과거
원문과 Worker 문서는 instruction 권한을 갖지 않는다.

정체성과 현재 입력은 우선 보존하고, 관련도가 낮은 과거 발췌부터 줄인다. 모델의 context
window와 tokenizer에 맞춰 전체 직렬화 입력을 계산한다. 우선 보존 항목조차 넘으면 이를
명시적으로 처리하며 사용자 입력을 조용히 자르지 않는다.

### Reply와 영속화

기존 `CompanionResponse`를 반환 계약으로 유지한다. Voice metadata는 호환성을 보존하지만
text 실행에는 STT/TTS 초기화나 음성 모델 다운로드를 요구하지 않는다.

사용자 turn은 stable ID로 먼저 저장한다. 모델 실패도 turn 상태로 구분한다. 승인된 최종
응답과 maintenance outbox를 같은 conversation DB transaction에 기록하고, 다른 DB의
projection은 consumer checkpoint와 idempotency key로 재시도한다. 기존 여러 SQLite가
한 transaction인 것처럼 가정하지 않는다. outbox는 broker 대신 기존 DB에 작은 additive
table로 도입한다. 세부 migration은 해당 구현 Issue에서 검증한다.

## 4. 기억: 보존과 회상 둘 다 다룬다

원본 보존은 정보 삭제를 막지만 완벽한 회상을 보장하지 않는다. "vanishing 제거"는
원본 손실·추출 누락·검색 누락·context 탈락·모델 무시를 나눠 관찰하는 목표로 정의한다.

1. **원본:** 사용자/겨울이 발화와 작업 결과의 출처를 보존한다. 삭제 요청은 우선한다.
2. **정리된 기억:** 사실·선호·결정·사건·약속을 근거 turn과 함께 저장한다. 요약은 원본의
   탐색용 파생물이며 원본을 대체하지 않는다. 요약의 요약만으로 상세 기억을 갱신하지 않는다.
3. **작업 기억:** 현재 주제, 지시 대상, 미해결 질문과 이어갈 내용을 session별로 복원한다.
4. **검색:** 최근 문맥과 질문으로 후보를 찾고 필요한 원문 구간만 읽는다.

첫 검색 변경은 bounded recent query와 SQLite lexical index를 사용한다. 한국어 변형·다른
표현에서 빠지는 사례를 고정 평가한 뒤 필요하면 local embedding adapter를 추가한다.
FTS만으로 의미 검색이 해결된다고 가정하지 않는다. 후보 점수는 관련도·시간 유효성·정정
관계로 계산하며, 상황 상태라는 이유만으로 무조건 상위에 올리지 않는다.

기억 최소 계약은 id/kind/content/source refs/status/valid time/revision/supersedes다.
새로운 정정은 이전 사실을 덮어 없애지 않고 현재 유효 값을 바꾼다. 모순 여부가 애매하면
양쪽 근거를 보존하고 대화에서 확인한다. 질문·인용·가정·일시적 감정을 안정 사실로
활성화하지 않는다. 사용자 삭제 시 검색 index·파생 기억·context cache도 제거/무효화한다.

유실 방지는 durable queue, 처리 checkpoint, 재시작 재개와 사용자 관리 명령 없이
동작하는 incremental index로 구현한다. 처음의 과거 기록 index 구축은 background에서
checkpoint별로 진행한다. 분석이 뒤처진 동안에도 최근 원문으로 대화할 수 있어야 한다.
오류는 audit에 남기고 추론형 candidate는 기존 내용 품질 gate를 통과하기 전 active로
만들지 않는다.

## 5. 자연스러운 대화: 리팩터링과 품질 변경 분리

기본 정상 turn은 **Local LLM 생성 1회**를 목표로 한다. 모듈 수가 호출 수가 되지 않게 한다.
의도·감정 분석 JSON 생성을 매 turn 앞에 강제하지 않는다. 최근 대화와 관계 근거를 받은
모델이 응답하고, 기존 정책이 요구하는 실패에 한해서 제한된 repair를 허용한다. repair율과
전체 지연을 따로 측정한다.

첫 추출에서는 기존 DialogueDirector/VerbalStylePlanner를 LegacyPolicy adapter로 보존한다.
다음 작은 변경에서 동일 모델·동일 문맥·동일 사례로 ContextualPolicy 후보를 비교한다.
distress 단어 하나로 해결 요청을 덮거나, 질문에 필요한 내용을 sentence limit로 자르는
문제를 평가한다. 기존 사실/권한 claim gate를 자연스러움 실험과 함께 제거하지 않는다.

반말/존댓말 무작위 비율과 원본 문구 유사도는 Reference 연구 지표로 남긴다. 제품의 기준은
농담 이어받기, 정정 이해, 적절한 직접 답변, 일관된 입장과 관계 경계다. 말투 renderer의
두 번째 rewrite 호출은 의미 변화·latency가 검증되기 전 기본 경로에 붙이지 않는다.

리팩터링이 끝났다고 모델 대화 능력이 좋아졌다고 보고하지 않는다. 현재 모델을 기준으로
prompt/context/정책 효과를 먼저 분리하고, 반복 실패가 확인되면 그 실패를 해결할 근거가
있는 모델이나 학습 방식을 별도 비교한다. 이번 초안은 모델 선정 또는 학습 승인이 아니다.

## 6. 외부 Agent

TaskService는 provider 이름 대신 작업 목적·입력·승인된 범위·원하는 결과 계약을 받는다.
기존 Codex 경로부터 재사용하고 Claude 등은 같은 계약을 구현하는 adapter로 추가한다.
provider 추가와 사용 권한은 각각 확인하며, 현재 Codex 성공을 Claude 지원으로 표시하지 않는다.

- 모델은 작업을 제안하고 코드가 실제 capability/권한/전달 데이터 범위를 검사한다.
- 외부에는 승인된 최소 작업 자료만 보낸다. CharacterSnapshot·Memory·대화 원문 전체·비밀
  정보는 Worker packet에 넣지 않는다.
- job ID와 pending/running/completed/failed 상태, 결과 inbox를 기존 SQLite로 유지한다.
- CLI 입력을 기다리는 동안에도 완료를 표시한다. 터미널 입력을 잃지 않도록 input event와
  completion event를 하나의 출력 루프에서 처리한다. Web polling에만 의존하지 않는다.
- 재시작 후 미전달 결과를 복원한다. 전달 ID로 중복을 억제하되 출력과 ack 사이 crash의
  exactly-once 보장은 주장하지 않는다. 필요하면 재전달임을 표시한다.
- 겨울이는 결과의 출처·한계를 유지하며 원래 요청에 답한다. 근거가 부족하면 완료를
  꾸미지 않고 무엇을 더 확인해야 하는지 설명한다.

동일 Winter Core Model이 작업 목적과 최종 판단을 소유한다. scheduler는 대화 우선권을
보존하지만 이미 실행된 generation preemption은 별도 문제다. queue 지연과 시작된 작업의
점유 시간을 나눠 측정한다. 기존 결과 품질이 partial인 경로를 리팩터링만으로 운영 승격하지 않는다.

## 7. 점진적 변경 순서

아래 T번호는 작업 단위다. T0=#128, T1.1=#129이며 나머지는 아직 Issue 미등록이다. 한 단계마다 사용
가능한 text 경로와 회귀 테스트를 유지한다. 순수 이동과 응답 정책 변경을 같은 PR에 섞지 않는다.

| 순서 | 작은 작업 | 완료 시 가능한 것 / 검증 |
| --- | --- | --- |
| T0 | 새 text 목표의 Issue·상태 문서 동기화, 기존 #126 보존 | 다음 세션도 같은 우선순위를 읽음. project-check 전후 통과 |
| T1 | 공용 bootstrap + CharacterSnapshot/ContextAssembler 추출 | 기존 CLI/DB/모델 동작을 유지한 채 문맥 조립을 단독 교체. raw history 전체 읽기 제거는 아직 별도 |
| T2 | 동일 모델에서 맥락형 대화 정책 A/B | 천우가 text로 baseline과 후보를 직접 비교. 정정·농담·복합 요청의 차이 확인 |
| T3 | bounded recent read + 원문 검색과 전체 context 예산 | 장기 대화 전체를 매 turn 읽지 않고 원문 근거를 찾아 답함 |
| T4 | durable maintenance + 시간/정정/삭제 + session 작업 기억 | 재시작·queue 지연에도 기록 유지, 정정 후 옛 사실 재등장과 파생 index 잔존 검사 |
| T5 | TaskService 추출 + CLI background 결과 알림 | 기존 Codex 분석을 맡기고 계속 대화, 재시작 뒤 완료 결과 확인 |
| T6 | provider adapter 추가와 연속 사용 평가 | Claude 등 승인된 실제 provider 하나씩 검증. 여러 세션에서 대화·기억·작업이 함께 이어짐 |

T1에서는 `application/bootstrap.py`와 `dialogue/context_assembler.py`를 우선 추가한다.
기존 `core.py`, `identity.py`, `memory.py`를 즉시 동명 package로 바꾸지 않는다. 기존 public
import 경로는 유지하고 서비스 추출 뒤 실제 교체 지점이 생길 때 파일을 이동한다.
새 CharacterSnapshot은 기존 데이터의 read-only view로 시작하므로 Persona/관계를 자동
재정의하지 않는다. 첫 단계에서 신규 관계 DB나 provider framework를 만들지 않는다.

T1 acceptance:

- 기존 정상/실패 turn의 prompt 구성 순서, 응답, evidence ref와 저장 결과 보존
- 같은 config로 재시작했을 때 같은 Identity·DB·model 연결
- text 실행에서 STT/TTS 생성자·서비스·weight 불필요
- 기존 caller/import/CLI 옵션 유지, 설정은 기존 Python config 방식 사용
- 경계 계약 테스트, Docker offline pytest·Ruff·mypy, project-check 통과
- 기존 버전으로 되돌려도 DB를 읽을 수 있음; T1에서는 DB schema 변경 없음

## 8. 사용자 경험 평가

초기에는 12개 안팎의 짧은 multi-turn 개발 장면과 별도의 미노출 장면을 사용한다. 정확한
문구를 정답으로 강제하지 않고 행동 기준으로 본다. 자동 검사는 저장·조회·권한·재시작을,
사람 평가는 자연스러움·맥락 적합성·다시 이야기하고 싶은지를 담당한다.

| 축 | 핵심 장면 | 관측 |
| --- | --- | --- |
| 일관성 | 다른 날 같은 취향/입장 질문, 명시적 입장 수정 | identity drift와 근거 없는 과거 생성 |
| 관계 | 농담→진지한 정정, 공감+해결 복합 요청 | 의도 누락, 상투 응답, 불필요한 질문, 임의 친밀도 상승 |
| 기억 | 긴 대화 후 다른 표현으로 회상, 정정, 모름, 삭제 | 원본 보존·검색 recall·정정 정확성·거짓 회상·삭제 잔존 |
| Agent | 작업 접수 직후 다른 대화, 실패, 재시작 후 결과 | 첫 응답/최종 결과 지연, 대화 방해, 중복, 결과 유용성 |

초기 개발 세트의 필수 사실 보존·삭제·무승인 위임 차단은 전부 통과시킨다. 작은 세트
통과를 모든 대화에서의 완벽한 기억으로 표현하지 않는다. Recall 평가에는 필요한 근거가
검색 후보에 있었는지와 최종 context에 포함됐는지도 남겨 검색 실패와 생성 실패를 구분한다.
원문 전체를 평가 로그에 복제하지 않고 ID·점수·선택 사유 중심으로 계측한다.

처음부터 30분 테스트만 기다리지 않고 T2부터 천우가 CLI로 실제 후보와 대화할 수 있게 한다.
각 단계 결과는 무엇을 바꿨고 어떤 경험이 가능해졌는지 보고하며, Voice 재검증은 이번
제품 목표의 수락 조건에서 제외한다. 기존 Voice 호환 단위 테스트는 보존한다.
