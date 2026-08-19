# winter-ai

`winter-ai`는 한 명의 사용자 **천우**와 장기간 생활의 맥락을 이어 가는 로컬 중심
Personal Companion, **겨울이**를 만드는 프로젝트다.

겨울이는 범용 Agent에 캐릭터를 씌운 제품이 아니다. 대화, 기억, 관계, 실제로 함께 겪은
사건과 시간이 제품의 중심이고, 검색·개발·자동화 Worker는 그 관계를 지원하는 하위
capability다. CLI와 폰 Voice는 서로 다른 겨울이가 아니라 같은 `CompanionCore`, Identity,
Memory와 시간축을 공유한다.

최상위 제품 계약은 [Winter V1 목표](docs/winter-v1-goal.md), 개발·안전 규칙은
[AGENTS.md](AGENTS.md), 기능별 실제 성숙도는
[V1 Maturity Ledger](docs/v1-maturity-ledger.md)를 기준으로 한다.

개발 단계와 선행조건의 기계 판독 기준은 [`project-state.toml`](project-state.toml)이다.
새 기능을 시작하거나 상태를 바꾸기 전후에 `./winter project-check`를 실행한다. 이 검사가
실패하면 새 기능을 확장하지 않고 Issue·근거·문서 상태부터 바로잡는다.

<!-- winter-project-state: capability_registry=operational, development_control_gate=operational, improvement_ledger=operational, self_model=operational, work_orchestration=shadow -->

## 1. 겨울이는 무엇인가

겨울이가 제공하려는 경험은 다음과 같다.

- 프로그램을 껐다 켜도 같은 존재와 대화하는 느낌이 유지된다.
- 천우가 별도 관리 작업을 하지 않아도 평소 대화가 기록과 내부 분석으로 이어진다.
- 천우에 대한 사실과 겨울이 자신의 관점을 구분한다.
- 지금 가능한 능력과 불가능한 능력을 실제 runtime 근거로 설명한다.
- 과거 결정, 실패, 정정과 미완성 이야기를 다음 날에도 이어 간다.
- 음성은 단순 낭독이 아니라 동일한 정체성과 말하기 행동의 출력이다.
- 반복되는 한계는 천우와 토론하고, 승인된 변경만 Worker가 격리 구현한다.

다음은 겨울이가 아니다.

- 모든 요청을 수행하는 범용 자율 Agent
- system prompt 한 장으로 흉내 낸 캐릭터
- 모든 대화를 사실로 저장하는 Vector DB 챗봇
- 화면 밖에서 보내지 않은 하루나 감정을 꾸며 내는 역할극
- 사용자 승인 없이 자기 코드·Identity·권한을 바꾸는 시스템

V1의 목표 자율성은 **Level 3 — 천우가 승인한 변경만 Worker가 구현하고, 검증과 별도 채택
승인을 거쳐 실제 능력이 되는 수준**이다.

## 2. 지금 실제로 가능한 것

| 영역 | 현재 상태 | 실제 가능한 범위 | 핵심 한계 |
| --- | --- | --- | --- |
| CLI 대화 | 운영 | Local LLM과 지속 대화, 모든 SQLite 상태 공유 | 장시간 자유 대화 평가는 부족 |
| Phone Web/Voice | 제한 운영 | push-to-talk → STT → Core → 문장 단위 음성 재생 | VAD·barge-in 없음, STT와 음질 미성숙 |
| Conversation | 운영 | CLI·Web·Voice 원문을 하나의 DB에 순서대로 보존 | 전체 원문을 매 turn prompt에 넣지 않음 |
| Direct Memory | 제한 운영 | 명시적 `기억해`, exact evidence가 있는 좁은 named fact·안정 preference·결정 저장과 수정 이력 | broad 자기규정은 즉시 저장하지 않으며 추론형 자동 기억은 gate 실패로 미연결 |
| Current State | 검토형 | 현재 상태와 과거 상태 Timeline 유지 | Local 분석 정확도가 activation gate 미달 |
| Open Loop | 운영 | 나중에 이어갈 주제와 재시작 callback | 간접 약속·자동 완료 판정 부족 |
| TurnUnderstanding | Shadow | 의도·감정·시간 범위·제안 구조 기록 | held-out 5/10, 응답과 Memory에 미연결 |
| OutcomeEvaluator | Shadow | 이전 답변과 다음 천우 반응을 연결해 평가 | locked 18/30, 상시 worker 소비 미연결 |
| Background Maintenance | Shadow 운영 관찰 중 | 전용 CPU 모델이 응답과 분리되어 TurnUnderstanding queue를 자동 소비 | 5건 copied-DB·1건 live canary만 통과, 24시간 관찰과 결과 품질 audit 전 |
| Grounded Self-Presence | 제한 운영 | 직접 초대받은 턴에서 겨울이 쪽 판단·가치·반응 표현 | 사람 판정 4/12, 완성된 자아가 아님 |
| Response Claim Gate | 제한 운영 | 최종 답변의 신체 행동·바깥 사건·기억·외부 능력을 deterministic evidence와 대조 | locked-v2 14장면과 실제 Core 임시 평가 범위이며 범용 사실성 보장 아님 |
| SelfModel / Capability Registry | 제한 운영 | Identity와 health·test 근거가 있는 실제 대화·Memory 능력을 매 turn 읽고, 등록되지 않은 기능 주장을 차단 | runtime 장애 자동 degraded 전이와 capability 채택 cycle은 아직 없음 |
| Improvement Ledger | 기반 운영 + lifecycle Shadow | 문제 근거·토론·proposal revision·구현 승인에 이어 Fake Worker 결과·독립 검증·별도 채택 승인·rollback을 재시작 뒤에도 공유 | 실제 Worker/source/Capability 연결과 자동 대화 추출은 금지 |
| Winter Event/Day | turn 기록 운영 | 실제 turn을 한국 시간의 겨울이 하루로 기록 | 기존 대화 소급 없음, event 종류 확장 중 |
| Winter Belief | manual 운영 / autonomous Shadow | 근거·수정 이력, 저위험 자동 activation 계약 | LLM 후보와 activator 운영 연결 전 |
| Voice Identity | 제한 운영 | Chatterbox S3Gen Reference 직접 합성 | 사람으로 착각할 자연스러움에 미달 |
| Worker | read-only 제한 운영 + write Shadow | 명시적으로 부른 Codex 분석. 별도 합성 경로에서는 승인 파일만 격리 수정·독립 검증하고, exact patch 설명·채택·실제 적용·rollback 상태를 SQLite에 보존 | write Shadow는 대화·실제 Winter 검증 profile에 미연결, 실제 작업 평가 없음 |
| Winter Work Orchestration | Shadow, semantic activation 실패 | V2.1 계약, Qwen 14B·30B-A3B와 Gemma 12B·27B semantic-v2, typed Codex provider. shell/web 없는 MCP로 목록·검색·텍스트 읽기만 허용하고 stable evidence/audit 검증 | 네 후보 모두 ambiguity 0/3·review assessment 3/6. Qwen 30B 46/66, Gemma 27B 44/66·27초로 크기 확대도 실패. synthetic live 1건뿐이며 기존 Codex V0와 현재 답변에는 미연결 |

“구현됨”과 “완성됨”은 다르다. 단일 테스트나 한 번의 성공으로 성숙도를 올리지 않으며,
실패한 평가도 [Maturity Ledger](docs/v1-maturity-ledger.md)에 기준선으로 남긴다.

## 3. 천우가 사용하는 방식

정상 사용에서 천우가 하는 일은 **겨울이와 대화하는 것뿐**이다.

```text
천우가 말한다
→ 겨울이가 현재 대화·기억·미완성 이야기를 참고해 답한다
→ 대화와 실제 사건이 자동 저장된다
→ 매 turn 같은 Capability Registry에서 실제 가능·불가능을 읽는다
→ 진행 중인 개선 안건은 읽기 전용 Improvement snapshot으로 이어진다
→ 응답과 분리된 Shadow 분석이 의도·결과·후보를 관찰한다
→ 확실한 직접 정보만 승인된 정책으로 최신화한다
→ 애매한 내용은 겨울이가 이후 대화에서 자연스럽게 확인한다
```

`overview`, `reflect`, `memories`, `lived`, `capabilities`, `improvements` 같은 명령은 개발·audit·복구 도구다. 천우의
일상 사용 조건이 아니다.

### Text CLI

Orin llama-server와 SSH tunnel이 실행 중일 때:

```bash
./winter start
```

이미 실행 환경이 준비된 상태에서 다시 대화하려면:

```bash
./winter chat
```

Codex 읽기 전용 Worker를 함께 켜려면 다음처럼 시작한다. 일반 대화는 외부로 보내지 않으며,
`코덱스로 이 코드 원인을 분석해줘`처럼 Worker 이름과 작업을 함께 말한 턴만 위임된다.

```bash
./winter chat \
  --codex-worker \
  --codex-executable /home/hanelso/.nvm/versions/node/v20.20.2/bin/codex
```

Codex 결과는 `answer / evidence / limitations`로 검증되고 겨울이가 출처를 밝힌 뒤
Conversation DB에 같은 응답으로 저장한다. 실패하면 Local LLM으로 몰래 바꾸지 않는다.

현재 이 Codex 경로는 V0다. Worker 결과를 겨울이 말투로 직접 전달하는 수준을 넘어서기 위해
다음 Shadow 경로를 구현했지만 아직 운영 답변에는 연결하지 않았다.

```text
천우의 말
→ 같은 Winter Core Model이 목적·완료 기준·비공개 경계를 정리
→ Worker에는 필요한 작업 문서만 전달
→ Worker의 주장·근거·검사 결과를 보고서로 수신
→ 같은 Winter Core Model이 완료/재작업/천우 확인을 판단
→ 겨울이의 결론으로 대화
```

Worker 이름을 고르고 보고서를 그대로 중계하는 주체는 천우가 아니라 겨울이다. 다만 V0의
권한은 계속 project read-only이며, 모델별 held-out 평가를 통과하기 전에는 실제 요청을 이
경로로 자동 전환하지 않는다. 상세 계약과 gate는
[Winter-owned Work Orchestration](docs/work-orchestration.md)에 있다.

새 Orchestration용 typed gateway는 arbitrary shell을 주지 않고 `list_files`,
`search_text`, `read_text`만 제공하며 network/write와 project 밖 경로를 구조적으로
거부한다. 새 Shadow provider adapter는 Codex 부모의 인증/API 통신과 모델이 보는 MCP
도구를 분리하고, MCP를 빈 environment로 시작한다. synthetic gpt-5.4 1건에서 목록·읽기와
발급 근거 검증까지 성공했지만 공격 suite·실제 20 task 전이므로 기존 사용자-facing Codex
route에는 연결하지 않았다.

실제 수정 능력의 첫 Shadow 수직 단면도 분리해서 구현했다.

```text
구현 승인 + 정확한 파일/line 범위
→ 개인정보 없는 baseline과 writable copy
→ Codex가 copy 안에서 수정·로컬 테스트
→ Winter가 network·HOME 없는 sandbox에서 독립 unittest·Python 구조를 재검증
→ 실제 diff·검증 범위·한계를 Winter 설명으로 구성
→ source는 건드리지 않고 patch와 설명을 SQLite에 보존
→ 천우의 채택/재작업 결정을 exact patch hash에 묶어 기록
→ 채택 뒤 대상 파일이 그대로일 때만 임시 파일 교체로 적용
→ 적용 전 bytes를 보존하고, 실패 또는 rollback 때 복원
```

합성 산술 버그에서 한 줄 교체, Codex 내부 unittest 2개, host 재검증 2개와 중복 정의 검사를
통과했고 원본은 그대로였다. process 재시작 뒤에도 같은 patch·설명·상태가 남으며, 천우의
`적용해`/`다시 고쳐줘` 결정도 기록할 수 있다. 채택된 patch는 대상 파일의 exact baseline이
같을 때 실제 적용되고, 적용 후 새 편집이 없으면 재시작 뒤에도 rollback할 수 있다. 합성
실제 probe에서 적용과 원본 복원을 완주했다. 다만 아직 일반 대화에서 이 경로를 호출하지
않고 실제 Winter 전체 검증 profile도 없다. 따라서 현재 폰에서 “고쳐줘”라고 말하면 이
경로가 실행되는 상태는 아니다. 격리 경계는 [ADR-0018](docs/adr/0018-isolated-codex-implementation-proposal.md),
설명·영속화는 [ADR-0019](docs/adr/0019-persist-and-explain-change-proposals.md), 적용·rollback은
[ADR-0020](docs/adr/0020-fail-closed-change-adoption-and-rollback.md)을 따른다.

기본 Local LLM 주소는 `http://127.0.0.1:18080`이다. 다른 주소는 `.env`의
`WINTER_LLM_URL`로 명시한다. Local 모델이 실패해도 fake 답변으로 몰래 전환하지 않는다.

### Phone Web/Voice

폰 브라우저에서 버튼을 누르고 말하면 다음 경로가 동작한다.

```text
이어폰/폰 마이크
→ Browser MediaRecorder
→ WebM/Opus upload
→ 16 kHz mono WAV 변환
→ SenseVoice STT
→ 같은 CompanionCore
→ Local LLM streaming response
→ Chatterbox S3Gen direct voice
→ SSE 문장 단위 전송
→ 폰/이어폰 재생
```

Web은 user systemd service로 실행한다.

```bash
systemctl --user status winter-web.service
curl http://127.0.0.1:8000/health
```

서비스 기준 파일은 [deploy/systemd/winter-web.service](deploy/systemd/winter-web.service)다.
Web은 `127.0.0.1:8000`에만 bind하고, 폰 외부 접속은 Tailscale Serve HTTPS가 loopback으로
전달한다. POST body는 기본 16 MiB로 제한한다. 운영 반영과 확인 절차는
[Web ingress safety](docs/web-ingress-safety.md)를 따른다. 공개 인터넷이나 LAN에 인증 없는
포트를 직접 열지 않는다.

Web은 Shared Core 생성부터 음성 전달과 terminal event까지 한 turn씩 처리한다. Core 또는
음성 경로가 실패하면 빈 답변을 성공으로 끝내지 않고 명시적 SSE error를 보낸다. 동시 요청과
실패 경계는 [Web turn safety](docs/web-turn-safety.md)를 따른다.

## 4. 전체 동작 구조

```text
┌──────────────── Interfaces ────────────────┐
│ CLI                 Phone Web / Voice      │
│ text                 mic, transcript, SSE  │
└───────────────┬───────────────┬────────────┘
                └───────┬───────┘
                        ▼
                ┌ CompanionCore ┐
                │ Identity      │
                │ Context       │
                │ Memory recall │
                │ Dialogue plan │
                │ Local LLM     │
                │ Review/repair │
                │ Worker route  │
                └───────┬───────┘
                        │
     ┌────────────┬────────────┬────────────┬────────────┐
     ▼            ▼            ▼            ▼
 SQLite state  Shadow       Voice        Read-only Worker
 Conversation  Turn         Prosody      explicit opt-in
 Memory        Outcome      Chatterbox   Codex → evidence
 Belief        Reflection   SSE player   + limitations
 Open Loop
 Winter Event
```

Domain/Core는 llama.cpp, SenseVoice, Chatterbox 같은 구체 runtime을 직접 import하지 않는다.
Port/Adapter 경계로 모델을 교체하며 CLI와 Voice는 반드시 같은 Core를 사용한다.

### 한 번의 text turn 내부 순서

1. 사용자 원문을 Conversation DB에 저장한다.
2. 명시적 Memory, exact conversation evidence가 있는 좁은 직접 진술과 Open Loop를
   deterministic 정책으로 확인한다.
3. 관련 active Memory, Current State, Belief, 과거 turn과 Open Loop를 조회한다.
4. `DialogueDirector`가 `listen`, `state_view`, `self_reciprocate` 같은 대화 행동을 고른다.
5. Identity → Memory → Belief → recall → style → grounding 순으로 context를 만든다.
6. Local LLM이 답하고 `ResponseReviewer`가 관찰 가능한 실패를 한 번만 repair한다.
7. 최종 답변을 저장하고 사용자에게 반환한다.
8. 완료 turn을 privacy-minimal `Winter Event`로 기록한다.
9. TurnUnderstanding과 Outcome queue는 응답을 막지 않는 Shadow 경로에 남긴다.

### Voice turn의 차이

Voice는 text turn 앞에 STT가, 뒤에 TTS가 붙을 뿐 의미 응답을 다시 작성하지 않는다.
CLI에 보이는 lexical response와 Voice가 말하는 문장은 동일해야 한다. `ProsodyPlan`은
감정·속도·에너지·pitch 계약을 제공하지만 현재 TTS가 모든 값을 완전히 실현하지는 못한다.

## 5. 지속 상태와 소유권

서로 다른 종류의 정보를 한 저장소나 prompt에 섞지 않는다.

| 상태 | 뜻 | 변경 주체와 규칙 |
| --- | --- | --- |
| Core Identity | 이름·역할·가치·관계 경계 | 천우 승인 없이 자동 변경 금지 |
| Conversation | 실제 대화 원문 | append-only 기록, 사용자 조회 가능 |
| User Memory | 천우에 대한 사실·선호·결정 | 직접 진술 또는 검토된 후보만 active |
| Current State | 요즘의 멘탈·회사·운동 등 변화 가능한 상태 | 최신 값 사용, 이전 값은 History 보존 |
| Open Loop | 나중에 이어갈 이야기 | 해결·dismiss 전까지 지속 |
| Winter Belief | 겨울이 자신의 수정 가능한 판단 | 근거·confidence·반증·revision 필수 |
| Winter Event | 실제로 발생한 대화·평가·결정 | immutable 사실, 해석은 별도 |
| Capability | 실제 할 수 있는 기능 | health·test·채택 근거 없이는 available 금지 |
| Improvement | 문제→제안→구현→채택 과정 | 천우 승인과 rollback 필요 |

기본 Host 데이터 경로는 `data/`이며 Git에서 제외된다.

| 파일 | 내용 |
| --- | --- |
| `identity.json` | 개인 Core Identity |
| `conversations.sqlite` | CLI·Web·Voice 대화 원문 |
| `memories.sqlite` | 사용자 Memory와 Current State History |
| `beliefs.sqlite` | Winter Belief와 원자적 activation audit |
| `dialogue_state.sqlite` | Open Loop |
| `turn_understanding.sqlite` | Shadow turn 분석 |
| `outcomes.sqlite` | 이전 답변과 다음 반응의 Shadow 결과 |
| `reflection.sqlite` | 과거 대화 Memory 후보 |
| `belief_reflection.sqlite` | Winter Belief의 Shadow proposal와 safety 결과 |
| `day_reflections.sqlite` | 실제 하루를 다음 대화로 잇는 근거 기반 회고 |
| `winter_events.sqlite` | 겨울이의 실제 사건과 날짜별 시간축 |
| `capabilities.sqlite` | 실제 기능, limitation, health·test 근거와 lifecycle |
| `improvements.sqlite` | 개선 문제·근거·proposal version/hash·구현 승인과 transition history |
| `voice_latency-*.sqlite` | 원문 없는 Voice 구간 latency |

## 6. Memory가 동작하는 방식

### 즉시 저장 가능한 것

- `기억해. 나는 Python config를 선호해` 같은 명시적 저장 요청
- `나는 결론부터 듣는 설명을 좋아해` 같은 안정적인 1인칭 직접 진술
- `내 생일은 3월 12일이야` 같은 이름이 정해진 사실 field
- `우리는 SQLite로 하기로 했어` 같은 직접 결정

질문, 추측, 제3자 사실, `오늘/지금`의 순간 상태와 일반적인 자기평가는 안정 Memory로 자동
저장하지 않는다. 새 자동 기억은 정확한 `conversation:<message_id>` 근거와 시간 범위를
보존한다. 근거가 없던 기존 row는 내용을 추측해 backfill하지 않고 `unknown`으로 유지한다.
세부 계약은 [Direct Memory Evidence](docs/memory-evidence-temporal.md)를 따른다.

### Current State와 History

운동, 멘탈, 회사 스트레스처럼 현재 달라질 수 있는 정보는 영구 성격과 분리한다. 같은
topic의 새 상태가 승인되면 이전 active 값은 `deprecated`가 되고 History로 남는다.
평범한 대화에는 최신 값만 사용하며 변화를 물을 때만 과거 값을 함께 조회한다.

```bash
./winter state
```

### Reflection

Local LLM Reflection은 과거 문맥에서 장기 후보를 찾지만 원문 evidence를 시스템이 고정하고,
별도 verifier가 직접성·지속성·현재 상태 grounding을 확인한다. 현재 held-out gate에
실패했으므로 자동 반영하지 않는다. 분석용 LLM은 대화용 Orin을 자동 재사용하지 않는다.

```bash
WINTER_ANALYSIS_LLM_URL=http://127.0.0.1:18081 ./winter reflect
./winter memories
./winter overview
```

## 7. 겨울이의 하루와 자율 관점

겨울이의 하루는 생성형 일기가 아니다. 한국 시간 안에서 실제로 발생한 다음 사건의
집합이다.

- CLI·Web·Voice 대화 참여
- 답변에 대한 후속 결과
- 평가 성공과 실패
- 실제 결정과 관점 revision
- 검증·채택된 capability

대화가 없던 시간에 한 활동, 몸의 경험, 감정이나 기억을 만들지 않는다. 기존 대화를 오늘
일로 소급하지도 않는다.

개발자는 원문을 읽지 않고 다음처럼 상태를 확인할 수 있다.

```bash
./winter lived
./winter lived --winter-day 2026-08-17
./winter days
./winter viewpoints
```

### 하루가 다음 날로 이어지는 방식

매일 04:10(한국 시간)에 작은 oneshot 작업이 끝난 실제 하루 하나를 정리한다. 천우가 할
일은 없다. 그날 실제 양방향 대화와 Winter Event가 모두 없으면 아무것도 만들지 않는다.

```text
함께한 일 / 천우 변화 관찰 / 이어갈 이야기 / 겨울이의 생각 / 다음 대화 단서
```

각 종류는 최대 하나이며 모든 문장은 같은 날의 conversation message id를 근거로 가진다.
저장된 회고는 다음 CLI·Web·Voice가 공통으로 읽지만, 영구 사실이 아니라 수정 가능한
지난날 회고로 표시된다. 현재 천우의 말과 충돌하면 현재 말을 우선한다. Day Reflection은
Memory·Belief·Capability를 자동 활성화하지 않는다.

현재 과거 대화에는 Winter Event가 없으므로 소급해 겨울이의 하루로 꾸미지 않는다. 첫
실제 결과는 새 Event가 기록된 하루가 끝난 다음 생성된다. 자세한 경계는
[Day Reflection](docs/day-reflection.md)과 [ADR-0013](docs/adr/0013-daily-continuity-before-autonomous-analysis.md)에 있다.

### Winter Belief lifecycle

```text
actual events
→ local LLM proposal (Shadow)
→ independent semantic safety review (Shadow)
→ activation policy
→ active
→ counterevidence / revision
→ deprecated history
```

자동 activation 정책은 다음을 모두 요구한다.

- `conversation:`, `relationship:`, `project:`의 제한된 Winter-owned subject
- confidence 0.80 이상
- 같은 subject의 exact Event 3개 이상
- 한국 시간 기준 2일 이상
- `accepted/continued + helpful`인 supportive Outcome 1개 이상
- 기존 active 관점을 바꿀 때 정확한 `supersedes`

Identity, User Memory, Capability, permission/security, Worker 실행과 외부 write는 이
경로를 사용할 수 없다. 같은 하드코딩 행동이 반복됐다는 사실만으로도 활성화할 수 없다.

현재 구현은 다음 경계에 있다.

- activation 결정과 Belief 변경은 `beliefs.sqlite`의 한 transaction으로 보존된다.
- Event 미러가 실패해도 결정은 유실되지 않고 같은 결과로 재시도할 수 있다.
- Local LLM proposal과 독립 safety review의 structured contract가 있다.
- safety review는 Winter-owned, evidence entailment, user fact, Identity, capability,
  dependency/coercion과 counterevidence를 각각 판정한다.
- 실제 완료 Outcome과 Winter Event를 subject별로 묶는 privacy-safe evidence builder가 있다.
  답변과 후속 발화 원문은 분석 요청 동안만 읽고 Shadow DB에 다시 복사하지 않는다.
- 같은 evidence window는 재시작 후에도 같은 ID를 가지며 한 번만 분석된다.
- 안전하게 판정된 proposal도 현재는 Shadow DB에만 남고 active Belief가 되지 않는다.
- 20개 locked-v1 semantic safety scene과 실행기가 있다. 아직 실제 별도 분석 모델 결과가
  없으므로 proposal→candidate와 activator 연결은 금지 상태다.

별도 분석용 로컬 모델을 준비한 개발 환경에서만 다음을 실행한다. 대화용 Orin 주소는
분석 부하로 재부팅된 기록 때문에 자동 재사용하지 않는다.

```bash
WINTER_ANALYSIS_LLM_URL=http://127.0.0.1:PORT ./winter viewpoint-shadow
PYTHONPATH=src python experiments/belief_safety_eval.py \
  --url http://127.0.0.1:PORT \
  --output review/belief-safety-locked-v1.md
```

설계와 이유는 [SelfModel](docs/self-model-design.md),
[ADR-0011](docs/adr/0011-evidence-backed-lived-continuity.md),
[ADR-0022](docs/adr/0022-evidence-gated-capability-registry.md)을 따른다.

현재 capability와 improvement 상태는 개발·audit가 필요할 때만 다음처럼 확인한다. 이
명령들은 항목을 추가하거나 활성화하지 않는다.

```bash
./winter capabilities
./winter improvements
```

## 8. 대화 행동과 자기 표현

`DialogueDirector`는 발화를 다음과 같은 관찰 가능한 행동으로 나눈다.

- `listen`: 해결책보다 현재 정서와 이유를 먼저 듣는다.
- `state_view`: 천우에게 맞춰 동의하지 않고 겨울이의 판단을 먼저 말한다.
- `self_reciprocate`: 천우가 겨울이를 대화에 포함했을 때 자기 쪽 내용을 말한다.
- `recall_memory`: active Memory에 근거해 직접 답한다.
- `continue_thread` / `gentle_callback`: 미완성 이야기를 이어 간다.
- `explain`: 답부터 말하고 필요한 이유를 설명한다.

`self_reciprocate`는 칭찬, 동의, 질문만으로 대화를 천우에게 되돌리면 실패다. 겨울이는
Identity, active Belief 또는 방금 대화에 근거한 현재 반응·선택·가치·한계를 하나 말해야
한다. 화면 밖 활동, 신체 경험과 수행할 수 없는 약속은 만들지 않는다.

현재 이 기능은 제한 운영이다. locked-v1 사람 판정은 4/12였으며 지속되는 SelfModel과
다양한 active Belief가 아직 부족하다.

## 9. 현재 모델과 음성 경로

| 구성요소 | 현재 선택 | 연결 |
| --- | --- | --- |
| Conversation LLM | Orin local llama-server, `A.X-4.0-Light-Q4_K_M.gguf` | SSH loopback `127.0.0.1:18080` |
| STT | SenseVoiceSmall Q8 GGUF CPU | localhost `127.0.0.1:8095` |
| TTS/Voice | Chatterbox Multilingual V3, S3Gen Reference direct | localhost `127.0.0.1:8093` |
| Legacy STT | whisper.cpp | 명시적 fallback `127.0.0.1:8094` |
| Legacy VC | Seed-VC | rollback 경로, 자동 fallback 금지 |

Voice Identity, Prosody와 Verbal Style은 별도 계약이지만 실제 발화에서는 하나의 말하기
행동으로 결합한다. 특정 Human Reference는 사용자가 선택한 개인용·비공개 기준선이며,
원본과 파생 데이터는 승인된 로컬 외장 저장소에만 둔다. 최종 겨울이는 Reference 복제가
아니라 별도의 `Winter Delta`를 가져야 한다.

현재 직접 Voice 경로의 실제 폰 4턴은 버튼 해제→재생 p50 4.41초, p95 5.03초,
release→STT p50 0.90초였다. 표본이 4/20이고 수동 transcript 2/4가 의미 있게 틀렸으므로
실시간 품질 gate를 통과한 결과가 아니다. 자세한 수치는
[Realtime Readiness](docs/realtime-readiness.md)에 있다.

## 10. Runtime 시작과 점검

### 필수 전제

- Python 3.11
- Docker Compose
- Orin llama-server와 SSH loopback tunnel
- Voice 사용 시 SenseVoice와 Chatterbox container
- 개인 `data/identity.json`
- 실제 모델·Reference 파일은 Git 밖의 로컬 경로
- Codex Worker 사용 시 로그인된 Codex CLI

처음 설치할 때는 `.env.example`을 `.env`로 복사하고 실제 Host 값만 로컬에서 수정한다.
`.env`는 commit하지 않는다. Identity 형식과 변경 경계는
[Identity 문서](docs/identity.md)의 JSON schema를 따르며 `data/identity.json`에 둔다.
Orin 연결은 인증 없는 LLM port를 공개하지 않고 [orin-tunnel.sh](scripts/orin-tunnel.sh)의
SSH loopback만 사용한다.

### Voice 서비스

```bash
docker compose -f compose.sensevoice-server.yaml up -d --build

CHATTERBOX_REFERENCE=/absolute/private/path/to/reference.wav \
  docker compose -f compose.chatterbox-reference.yaml up -d

curl http://127.0.0.1:8095/health
curl http://127.0.0.1:8093/health
systemctl --user restart winter-web.service
curl http://127.0.0.1:8000/health
```

Reference 절대경로를 Compose 파일, 로그나 Git에 넣지 않는다. Web 경로는
`--stt-backend sensevoice --no-voice-conversion`을 사용한다. 장애 시 fake, Whisper나
Seed-VC로 자동 전환하지 않고 오류를 드러낸다.

repository의 Web unit 변경은 실행 중인 대화를 끊지 않도록 자동 배포하지 않는다. unit을
설치·재시작한 뒤 `ss -ltn 'sport = :8000'`에서 loopback만 listen하는지 확인해야 한다.

현재 기준 systemd Web unit은 Codex read-only Worker를 함께 켠다. 폰에서 명시적으로
`코덱스로 ... 확인해줘`라고 말하면 기다리는 동안 진행 상태를 표시하고 결과를 같은
겨울이 음성으로 읽는다. Credential 원문은 겨울이 Memory나 Worker bundle에 넣지 않는다.
전체 권한 모델과 다음 write gate는
[Authority Delegation](docs/authority-delegation.md)을 따른다.

### 상태 점검 명령

| 명령 | 용도 | Local LLM 필요 |
| --- | --- | --- |
| `./winter status` | LLM과 Compose 서비스 상태 | 아니요 |
| `./winter lived` | 오늘 사건·Belief activation audit | 아니요 |
| `./winter state` | Current State Timeline | 아니요 |
| `./winter overview` | 대화·분석·Memory 전체 현황 | 아니요 |
| `./winter memories` | Reflection 후보 검토 | 아니요 |
| `./winter reflect` | 과거 대화 분석 | 별도 analysis LLM |

## 11. 개발 구조

```text
.
├── AGENTS.md                    제품·개발·안전 불변조건
├── README.md                    현재 제품과 운영 기준
├── winter                       사용자 launcher
├── pyproject.toml               Python/test/lint/type 설정
├── configs/                     model·verbal style profile
├── deploy/systemd/              실제 Host user service
├── docs/
│   ├── adr/                     변경 불가 설계 결정 기록
│   ├── winter-v1-goal.md        V1 제품 계약
│   ├── v1-maturity-ledger.md    근거·한계·다음 gate
│   └── ...                      기능별 설계와 평가
├── experiments/                 실제 model·voice·held-out probe
├── src/companion/
│   ├── core.py                  모든 interface가 공유하는 Core
│   ├── contracts.py             응답·음성 데이터 계약
│   ├── ports.py                 runtime 독립 Port
│   ├── cli/                     개발/텍스트 interface
│   ├── voice/                   Voice orchestration
│   ├── web.py                   Phone HTTP/SSE interface
│   ├── adapters/                llama.cpp·STT·TTS·SQLite adapter
│   ├── memory.py                사용자 Memory lifecycle
│   ├── beliefs.py               Winter Belief와 atomic audit
│   ├── winter_events.py         실제 사건과 하루
│   ├── belief_reflection.py     Shadow proposal와 semantic gate
│   ├── turn_understanding.py    Shadow turn 이해
│   ├── outcome.py               다음 반응 기반 평가
│   ├── work_orchestration.py    Winter 목적·Worker 보고·최종 판단 계약
│   ├── worker_gateway.py        provider-independent typed Worker 도구 계약
│   ├── isolated_work.py         구현 승인·diff·검증·채택 대기 계약
│   ├── change_proposal.py       exact patch·Winter 설명·결정 이력 저장
│   ├── change_adoption.py       stale 차단·실제 적용·rollback
│   ├── codex_gateway_mcp.py     인증 없는 typed project stdio MCP
│   ├── adapters/codex_gateway_worker.py  Codex control/tool-plane 분리
│   ├── adapters/codex_isolated_worker.py  폐기 가능한 copy의 수정·검증
│   └── reflection.py            과거 대화 Memory 후보
└── tests/
    ├── unit/                    인터넷·GPU·모델 없는 기본 계약
    ├── integration/             Shared Core와 persistence 흐름
    └── fixtures/                공개 가능한 synthetic fixture만
```

## 12. 개발과 검증

기본 테스트는 인터넷, GPU, 마이크, 실제 checkpoint와 API key 없이 실행돼야 한다.

```bash
docker compose run --rm dev pytest -q
docker compose run --rm dev ruff check .
docker compose run --rm dev mypy src
```

실제 모델·Voice 평가는 기본 회귀와 분리한다. 모델 실패를 fake 성공으로 바꾸지 않으며,
실행하지 않은 작업을 실행했다고 보고하지 않는다.

새 작업은 다음 순서를 따른다.

1. 관련 제품 문서와 현재 코드를 읽는다.
2. acceptance test와 실패 조건을 먼저 정한다.
3. 가장 작은 완전한 변경을 구현한다.
4. unit/integration, Ruff와 mypy를 실행한다.
5. 실제 모델 평가는 별도 marker·probe로 실행한다.
6. README, ADR과 Maturity Ledger를 같은 변경에서 갱신한다.
7. 현재 가능한 범위와 전체 성숙도를 구분해 보고한다.

## 13. Privacy와 안전 경계

- OpenAI, Anthropic, Gemini 등 외부 상용 대화 API는 초기 Companion 경로에서 사용하지 않는다.
- 대화 DB, 개인 Memory, 원본 음성·영상·자막, Human Reference와 checkpoint는 Git 제외다.
- 외부 Worker에는 관련 기술 scope와 sanitized project text만 전달하고 전체 Identity·대화 DB를 넘기지 않는다.
- Core Identity는 사용자 승인 없이 자동 변경하지 않는다.
- read-only Worker는 문장 안의 명시적 호출을 turn별 opt-in으로 본다. destructive/external write는 구체 대상에 대한 별도 승인이 필요하다.
- Capability는 가장 최근 health 성공과 test/evaluation 근거, available dependency가 없으면
  `available`이라고 말할 수 없다. claim scope는 실행 권한과 별개다.
- 모든 자동 판단은 evidence, confidence, status, version과 rollback 경로를 남긴다.
- 운영 adapter 장애는 명시적으로 드러내고 silent fallback하지 않는다.

권장 Git 제외 대상은 `.env*`, `data/`, `models/`, `checkpoints/`, `human_reference/`,
`generated_audio/`, SQLite와 media 파일이다. 작은 공개 synthetic fixture만
`tests/fixtures/`에 둘 수 있다.

## 14. 다음 개발 순서

현재 최우선은 관계와 기억을 유지하면서 겨울이가 실제 전문 작업의 목적과 최종 판단까지
소유하게 만드는 것이다.

1. 전용 CPU background maintenance 배포 — **초기 배포 완료, 24시간 Shadow 관찰 중**
2. persistent Capability Registry와 read-only SelfModel — **#119 범위 완료, 제한 운영 검증 중**
3. Improvement Ledger와 대화형 proposal state machine — **#120 기반 범위 완료**
4. Fake Worker 승인→독립 검증→별도 채택→rollback — **#121 Shadow 범위 완료**
5. Work Orchestration V2.1 안전 계약과 기존 모델 baseline — **구현 완료, activation 실패**
6. project 전체 stale 판정·process crash 복구·실제 Winter 검증 profile
7. prompt-injection·path/network/write 격리 공격 suite
8. 새 Core/runtime가 기존 ambiguity·grounding gate를 해결한다는 근거 확보
9. semantic·권한 gate 합격 후 read-only 실제 작업 20개를 사람 평가
10. 검증된 WinterDecision을 기존 Codex read-only route 앞뒤에 Shadow로 연결
11. 실제 작은 capability의 제안→승인→구현→검증→채택→재평가→rollback 완주

## 15. 주요 문서 인덱스

| 질문 | 기준 문서 |
| --- | --- |
| 이 프로젝트의 최종 V1 목표는? | [Winter V1 Goal](docs/winter-v1-goal.md) |
| 지금 실제로 어디까지 되는가? | [Maturity Ledger](docs/v1-maturity-ledger.md) |
| 다음 개발 순서는? | [Roadmap](docs/roadmap.md) |
| 겨울이에게 계정 권한을 어떻게 위임하는가? | [Authority Delegation](docs/authority-delegation.md) |
| 겨울이가 Worker를 어떻게 이해·지휘·검수하는가? | [Winter-owned Work Orchestration](docs/work-orchestration.md), [ADR-0015](docs/adr/0015-single-core-work-orchestration.md) |
| 자아·관점·하루의 경계는? | [SelfModel Design](docs/self-model-design.md) |
| 개선 문제에서 검증·채택·rollback까지 어떻게 이어지는가? | [ADR-0023 Improvement Ledger](docs/adr/0023-persistent-conversational-improvement-ledger.md), [ADR-0024 Fake Worker lifecycle](docs/adr/0024-fake-worker-level3-lifecycle.md) |
| 왜 자동 Belief에 이런 gate가 있는가? | [ADR-0011](docs/adr/0011-evidence-backed-lived-continuity.md) |
| LLM 관점 후보는 어떻게 검증하는가? | [Belief Reflection](docs/belief-reflection.md), [ADR-0012](docs/adr/0012-shadow-belief-semantic-review.md) |
| 어제의 겨울이가 오늘로 어떻게 이어지는가? | [Day Reflection](docs/day-reflection.md), [ADR-0013](docs/adr/0013-daily-continuity-before-autonomous-analysis.md) |
| 대화가 아직 왜 Agent처럼 느껴지는가? | [Self-Presence](docs/self-presence.md) |
| Memory와 현재 상태는 어떻게 다른가? | [Reflection Memory](docs/reflection-memory.md) |
| Phone Voice가 실시간인가? | [Realtime Readiness](docs/realtime-readiness.md) |
| 현재 Voice 경로 선택 이유는? | [Voice Path Decision](docs/voice-path-decision.md) |
| 모델·라이선스·artifact는? | [Model Selection](docs/model-selection.md) |
| Identity 파일은 어떻게 만드는가? | [Identity](docs/identity.md) |
| Human Reference 데이터 경계는? | [Human Reference Design](docs/human-reference-design.md) |
| Agent가 따라야 할 규칙은? | [AGENTS.md](AGENTS.md) |

## 16. 새 사람이나 Agent의 인수인계 순서

처음 저장소를 열었다면 다음 순서로 이해한다.

1. 이 README의 1~7절을 읽어 제품과 실제 사용자 흐름을 이해한다.
2. [AGENTS.md](AGENTS.md)를 끝까지 읽어 고정 요구사항과 금지 사항을 확인한다.
3. [Maturity Ledger](docs/v1-maturity-ledger.md)에서 성공과 실패 기준선을 함께 확인한다.
4. `git status --short`로 혼합 worktree를 확인하고 사용자·Claude·Codex 변경을 보존한다.
5. `./winter status`, `./winter lived`로 실제 runtime과 상태를 읽기만 한다.
6. 변경 전 관련 ADR·기능 문서와 테스트를 찾는다.
7. 하나의 작은 scope만 구현하고 offline 회귀를 통과시킨다.
8. 운영 반영 전 README·Ledger·rollback 영향을 확인한다.

새 Agent는 저장소를 다시 bootstrap하거나, prompt만 반복 수정하거나, 실패한 실제 모델을
fake adapter로 숨겨서는 안 된다.
