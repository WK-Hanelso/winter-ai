# Winter V1 Project Audit — 2026-08-19

- 상태: Baseline audit
- 범위: GitHub Issue/PR, Git worktree, README/AGENTS/V1 문서, 코드 경계, 테스트,
  실제 Host runtime
- 원칙: 이 문서는 현재 상태를 판정한다. 발견과 수정은 분리하며, 감사 중 제품 코드나
  운영 서비스를 변경하지 않는다.

## 1. 결론

Winter의 제품 방향은 유지되고 있다. Shared Core, 로컬 대화, persistent state, phone
push-to-talk, Human Reference voice와 명시적 Codex read-only 경로는 실제로 존재한다.

그러나 현재 개발 흐름은 V1 설계 순서와 일치하지 않는다.

1. V1이 먼저 요구한 `SelfModel`, `CapabilityRegistry`, `ImprovementLedger`는 구현되지 않았다.
2. 그 뒤 단계인 typed Worker gateway, 격리 수정, proposal persistence, 실제 적용과 rollback은
   Shadow 코드로 먼저 깊게 구현됐다.
3. 최근 기능 대부분은 GitHub Issue/PR 없이 한 장기 브랜치와 거대한 미커밋 worktree에 있다.
4. README와 테스트 일부가 실제 보장보다 강한 표현을 사용한다.
5. 운영 Web 경로에는 인증·실패 전파·동시성·실제 streaming과 prosody ownership 문제가 있다.

따라서 현재 상태는 **V1에 가까워지는 기능 실험들의 집합**이지, Level 3 V1이 순서대로
통합된 상태가 아니다. 새 모델이나 Worker 기능을 더 붙이기 전에 기준선을 고정하고 핵심
경계를 복구해야 한다.

## 2. 확인한 기준선

### Git과 GitHub

- local `main` / `origin/main`: `31c7223`, PR #110까지
- 현재 branch: `feat/voice-cloning-s1`, `de16bba`
- 현재 branch는 main보다 47 commits, 174 files, 약 24,194 insertions 앞서 있다.
- 현재 branch를 추적하는 GitHub PR은 없다.
- worktree에는 tracked modified 43개와 untracked 96개가 있다.
- GitHub Issue는 open 10개, closed 47개다.
- 오래된 draft PR #35, #63, #65는 open 상태다.

이는 AGENTS의 one-Issue / small-scope / test / docs / PR 흐름을 현재 변경들이 따르지 않았다는
뜻이다. 더 중요한 점은 실행 중인 Winter의 정확한 복구 기준 revision을 하나로 지목할 수
없다는 것이다.

### Offline 회귀

2026-08-19 현재 dirty worktree에서 실행했다.

```text
pytest: 671 passed
ruff: all checks passed
mypy: 160 source files, no issues
git diff --check: clean
```

회귀가 통과한다는 것은 계약 단위 코드가 안정적이라는 뜻이다. 실제 semantic grounding,
Web 인증, thread failure, callback timing, 30분 대화와 Level 3 전체 cycle이 검증됐다는 뜻은
아니다.

### 실제 runtime

- Conversation LLM: Orin `A.X-4.0-Light-Q4_K_M.gguf`, loopback `:18080`, ready
- STT: SenseVoice `:8095`, ready
- TTS: Chatterbox direct Reference `:8093`, ready
- Web: `winter-web.service`, active
- Tailscale Serve: tailnet HTTPS → `127.0.0.1:8000`, active
- Day Reflection timer: active, 실제 close 결과 0일
- background maintenance service: 설치되지 않음

현재 local state는 다음과 같다.

- Conversation message 307개
- TurnUnderstanding completed 0 / pending 189
- Outcome completed 0 / pending 59
- active Memory 10개
- active Winter Belief 0개
- Winter Event 0개
- closed Winter Day 0개

즉 평소 대화는 분석 queue에 자동 등록되지만 실제 background 분석은 소비되지 않는다.
Lived Continuity와 Day Reflection은 코드와 timer가 있으나 실제 continuity evidence는 아직
없다.

## 3. 설계 적합성 판정

| 영역 | 판정 | 실제 상태 | 핵심 차이 |
| --- | --- | --- | --- |
| 제품 Mission | 적합 | 관계 중심 Companion, Worker 종속 원칙이 문서와 코드에 유지됨 | 없음 |
| Local-first Core | 적합 | 대화 LLM·STT·TTS가 local runtime | 명시적 Codex Worker만 외부 경로 |
| Shared Core / state | 부분 적합 | CLI·Web·Voice가 `CompanionCore`와 같은 SQLite 기본 경로 사용 | 실제 Web/Voice가 Core의 prosody를 사용하지 않고 다시 계산 |
| Conversation persistence | 적합 | 재시작 뒤 같은 SQLite 사용 | model 실패 시 user-only dangling turn이 남을 수 있음 |
| Direct Memory | 부분 적합 | `기억해`와 일부 직접 진술을 즉시 active로 보존 | 자동 direct Memory에 message evidence, temporal scope, expiry, contradiction 필드 없음 |
| Current State | 부분 적합 | 최신 active + deprecated History 구조 존재 | `나는 실패한 사람이야`, `나는 운동 중이야`도 stable semantic으로 즉시 active 가능 |
| TurnUnderstanding | Shadow만 적합 | schema·queue·offline/held-out 평가 존재 | pending 189, 운영 consumer 없음, 응답 전에 사용되지 않음 |
| OutcomeEvaluator | Shadow만 적합 | next-turn 연결과 검토 계약 존재 | pending 59, 운영 consumer와 실제 blind audit 없음 |
| Grounding | 부적합 | 강한 system instruction은 항상 삽입 | 의미 출력 gate가 없어 신체·화면 밖 주장이 그대로 통과 |
| Dialogue review | 부분 적합 | 관찰 가능한 일부 실패를 repair | regex가 모르는 semantic 실패는 통과하고 두 번째 실패의 fallback도 일부 behavior에만 존재 |
| Voice vertical slice | 부분 적합 | phone PTT→STT→Core→TTS→SSE가 실제 동작 | VAD/barge-in 외에도 실제 first-sentence streaming과 shared prosody가 구현 설명과 다름 |
| Voice failure handling | 부적합 | CLI voice는 thread 오류를 caller에 전달 | Web thinking thread 오류는 빈 `done`으로 숨김 |
| Lived Continuity | 계약만 존재 | append-only Event와 day close 코드·timer 존재 | 실제 Event/Day 0, 3일 gate 검증 불가 |
| SelfModel | 미구현 | 설계 문서만 존재 | runtime capability/limitation snapshot 없음 |
| CapabilityRegistry | 미구현 | 문자열 capability enum과 문서만 존재 | health/evidence/version 기반 persistent registry 없음 |
| ImprovementLedger | 미구현 | improvement signal과 change proposal 일부 존재 | 대화의 문제→토론→승인 전체 lifecycle repository 없음 |
| Codex V0 | 제한 운영 | explicit name+action, sanitized bundle, read-only CLI | 결과를 Winter가 의미 검토하지 않고 prefix 후 전달 |
| Winter-owned orchestration | Shadow | typed contract·gateway·provider·평가 존재 | Core/대화/Registry/Ledger와 미연결, semantic gate 실패 |
| Level 3 전체 cycle | 미완료 | synthetic isolated patch apply/rollback은 존재 | 실제 대화 proposal→승인→capability registration/use/reevaluation은 없음 |
| Privacy in Git | 적합 | data, media, review, DB, `.env` 제외 확인 | 없음 |
| Local file privacy | 부적합 | Day Reflection과 생성 audio 일부만 `0600` | conversation, memory, belief DB와 identity가 현재 group/world-readable |
| Web access boundary | 부적합 | Tailscale HTTPS는 정상 | Web이 인증 없이 `0.0.0.0:8000`에 직접 listen하고 Codex Worker도 활성화 |
| Reproducible runtime | 부분 적합 | 일부 probe lock과 version 기록 존재 | production Chatterbox는 git `master`, legacy Whisper image는 `main-vulkan` floating |

## 4. 확인된 주요 결함

### P0 — Web 요청에 인증과 단일 turn 경계가 없다

`companion.web` 기본 bind는 `0.0.0.0:8000`이고 request authentication이 없다. Tailscale
Serve가 있어도 LAN에서는 8000으로 직접 접근할 수 있다. 운영 unit은 Codex Worker도 켠다.

또한 `Companion.answer()`는 thinking thread를 시작한 뒤 TTS 구간만 lock한다. 두 HTTP
요청의 Core generation과 conversation mutation은 동시에 실행될 수 있다. class docstring의
"one turn at a time"과 실제 lock 범위가 다르다.

필요한 gate:

- Web을 loopback에만 bind하고 Tailscale Serve만 ingress로 쓰거나, 강한 request auth 추가
- 전체 `Core → TTS → final persistence` turn을 한 queue/lock으로 직렬화
- request body size, STT upload size와 rate limit 명시

### P0 — Web LLM 실패가 성공처럼 끝난다

thinking thread의 예외가 저장되지 않는다. 실제 재현에서 `RuntimeError("llm failed")`가
thread traceback으로만 남고 client event는 다음 하나였다.

```text
done: {"text": ""}
```

이는 no-silent-fallback과 adapter failure handling 요구사항을 위반한다. Voice CLI에는 같은
오류 전달 코드가 있으나 Web에는 없다.

### P0 — Grounding은 정책 이름과 달리 instruction이다

`GroundingPolicy.instruction()`은 system text를 반환할 뿐 최종 응답을 승인하지 않는다.
`ResponseReviewer`는 신체·화면 밖 activity의 일반 의미를 검사하지 않는다. 실제 Qwen 문장
`나도 ... 따뜻한 차 한 잔을 꼭 마셔야겠어`는 reviewer에서 `None`으로 통과했다.

평가기의 `adds grounded first-person content`도 실제 grounding이 아니라 1인칭과 소수 금지
문구만 검사해 같은 문장을 PASS로 기록했다.

필요한 gate:

- 현재 lexical check 이름을 실제 측정 범위로 낮춰 표기
- final response의 claim kind와 evidence source를 분리
- 신체·외부 사건·기억·capability claim은 authorized evidence 없이는 발화 불가
- 같은 generator의 자기확인만으로 승인하지 않고 deterministic evidence authorization 사용

### P1 — 실제 Voice streaming과 shared prosody 계약이 깨져 있다

`respond_to_text_streaming()`은 model stream 전체를 모으고 review한 뒤 `on_sentence`를
호출한다. 따라서 문서와 test 이름이 말하는 "첫 문장이 쓰이는 동안 다음 문장 생성과 TTS를
겹친다"는 시간 순서가 현재 구현되지 않았다. 기존 test는 최종 callback 목록만 검사한다.

또 실제 `web.py`와 `voice_cli.py`는 `CompanionResponse.prosody`를 받지 못해 사용자 입력에서
`ProsodyPlanner`를 별도로 다시 실행한다. offline `VoiceOrchestrator`만 Core response의
prosody를 사용한다.

### P1 — 자동 Direct Memory가 V1 evidence 계약을 충족하지 않는다

현재 regex는 명시적이고 안정적인 1인칭 문장만 선택하려 하지만 의미 안정성을 보장하지
못한다. 아래 문장이 모두 즉시 confidence 1.0 active semantic Memory가 된다.

```text
나는 회사 때문에 지친 사람이야
나는 실패한 사람이야
나는 운동 중이야
```

Memory row에는 원문 conversation message id가 없고 temporal scope, valid-until,
contradiction도 없다. V1의 "모든 자동 기억에 원문 evidence"와 현재/안정 상태 분리 조건을
충족하지 않는다.

### P1 — V1 구현 순서가 역전됐다

V1 goal의 순서는 다음과 같다.

```text
SelfModel / CapabilityRegistry
→ ImprovementLedger
→ Fake Level 3 cycle
→ isolated work / adoption
→ real Worker
```

실제는 앞의 두 항목이 없는 상태에서 isolated implementation, proposal persistence,
adoption과 rollback이 먼저 구현됐다. 이 코드는 폐기할 필요는 없지만 더 확장하지 않고,
앞 단계의 실제 product state에 연결될 때까지 보존된 Shadow substrate로 취급해야 한다.

### P1 — GitHub와 현재 제품 상태가 분리됐다

- #27, #34는 main 기준 미완료지만 현재 장기 branch에는 다른 phone path가 존재한다.
- #53은 특정 Human Reference 복제를 제외하는 옛 scope이며 현재 accepted policy와 다르다.
- #59는 현재 Chatterbox/direct voice decision 이전의 CosyVoice probe다.
- #62, #64와 draft PR #63, #65의 slash command는 현재 branch 코드에 없다.
- #66과 #75 body/checklist는 현재 완료된 수집·평가 상태를 반영하지 않는다.
- #111 body는 `-` 하나뿐이라 acceptance나 decision context가 없다.
- 최근 V1, lived continuity, Worker orchestration 변경은 대응 Issue/PR이 없다.

## 5. README와 문서 판정

### 잘된 점

- README는 mission, 실제 사용 경로, state ownership, privacy와 maturity를 한 문서에서 찾을 수
  있게 한다.
- local Markdown link 0개가 깨져 있다.
- Maturity Ledger는 실패 결과와 activation 금지를 대체로 솔직하게 기록한다.
- ADR은 Reference, Memory/Belief, Voice와 Worker decision의 이유를 남긴다.

### 수정이 필요한 주장

- `Grounded Self-Presence`가 허구 활동을 repair한다는 표현은 prompt/lexical 범위로 낮춰야 한다.
- Phone path의 `Local LLM streaming response`는 HTTP streaming과 sentence-to-TTS overlap을
  구분해야 한다.
- "천우는 대화만 한다"는 목표와 queue 등록은 구현됐지만 background 소비는 미배포임을
  사용 흐름 바로 옆에서 더 분명히 표시해야 한다.
- roadmap의 `SelfModel·CapabilityRegistry — Lived Continuity 0.1 구현`은 서로 다른 기능을
  같은 행에 넣어 Capability Registry가 존재하는 것처럼 읽힌다. Ledger의 Level 0 판정과
  일치하도록 분리해야 한다.
- V1 goal의 `Immediate Next Action = TurnUnderstanding`은 이미 지난 기준이다. 현재 next
  action과 history를 분리해야 한다.
- `docs/architecture.md`, `docs/memory-design.md`는 제안 layout에는 있으나 실제로 없다.
  기존 기능 문서로 대체한다고 명시하거나 canonical 문서를 만들어야 한다.

## 6. GitHub Issue 정리 제안

외부 상태를 바로 변경하지 말고 다음 순서로 reconcile한다.

| 대상 | 제안 |
| --- | --- |
| #27 | current branch phone path와 main 차이를 기록하고 새 baseline PR 뒤 완료 재판정 |
| #34 / PR #35 | 오래된 구현을 merge하지 말고 현재 browser PTT 기준으로 scope 재작성 또는 supersede |
| #53 | Human Reference를 허용한 최신 policy에 맞춰 Epic 재작성 |
| #59 | 실제 CosyVoice 결과를 연결해 completed/abandoned/superseded 중 하나로 종료 |
| #62 / #64, PR #63 / #65 | 정상 사용이 아닌 audit UI 요구로 재판정; 필요 없으면 not planned, 필요하면 새 baseline 위 재구현 |
| #66 / #75 | 실제 corpus·voice·held-out 결과로 checklist와 current state 갱신 |
| #79 | production runtime pin보다 뒤의 재현성 backlog로 우선순위 조정 |
| #111 | 제목만 유지하지 말고 문제, 후보, 측정, acceptance를 작성하거나 새 issue로 교체 |

새 remediation issue는 최소 다음으로 분리한다.

1. P0 Web ingress authentication/bind, request limits와 turn serialization
2. P0 Web thread failure propagation regression
3. P0 semantic grounding claim/evidence gate와 평가기 정정
4. P1 automatic Memory evidence/temporal schema와 direct-statement safety
5. P1 actual sentence streaming callback와 Core-owned prosody
6. baseline consolidation: current branch + dirty worktree를 검토 가능한 작은 PR로 보존
7. read-only `SelfModel` + persistent `CapabilityRegistry`
8. persistent `ImprovementLedger` + conversational approval state
9. maintenance worker 24시간 Shadow deployment와 queue drain audit
10. 위 state 위에서만 Winter-owned Worker cycle 통합

## 7. 복구 순서

### Phase A — 멈추고 보존

- 새 모델·Worker 기능 추가를 중지한다.
- current branch와 dirty worktree를 삭제·reset하지 않는다.
- 파일 묶음을 기능별로 inventory하고 exact baseline을 만든다.
- GitHub Audit Epic과 위 remediation issue를 만든다.

### Phase B — 운영 P0

- Web ingress와 request auth/bind를 닫는다.
- thread 오류를 client error로 전달하고 dangling turn 정책을 정한다.
- 실제 one-turn serialization을 검증한다.
- grounding false PASS를 고친다.

### Phase C — V1 중심 state

- Memory evidence/temporal contract를 먼저 완성한다.
- read-only SelfModel과 CapabilityRegistry를 실제 health/evaluation에서 만든다.
- ImprovementLedger가 대화의 문제·승인·검증·채택을 영속화하게 한다.
- maintenance queue를 안전한 local analysis runtime으로 실제 소비한다.

### Phase D — 기능 재연결

- Voice callback timing과 shared prosody를 하나의 response contract로 연결한다.
- 기존 Worker Shadow substrate를 Registry/Ledger에 연결한다.
- 실제 작은 capability 하나로 proposal→implementation→verification→adoption→use→rollback을
  완주한다.
- 마지막에 30분 cross-interface 대화와 phone 20-turn을 다시 측정한다.

## 8. 감사의 최종 판정

현재 프로젝트를 폐기하거나 다시 bootstrap할 이유는 없다. Core와 persistent state, phone
voice, Reference 연구와 다수의 안전 계약은 재사용 가치가 높다.

하지만 지금 속도로 Shadow 기능을 더 추가하면 Winter 본체보다 주변 통제 코드가 계속
커진다. 다음 개발의 성공 기준은 새 파일 수가 아니라 다음 세 가지다.

1. 천우의 평범한 한 turn이 오류 없이 같은 Core/state를 지나가는가.
2. 겨울이가 말한 사실·기억·능력에 실제 근거가 있는가.
3. 대화에서 승인된 작은 개선 하나가 Registry와 Ledger를 거쳐 실제 사용·rollback까지
   닫히는가.

이 세 가지가 닫히기 전에는 Winter V1 또는 Level 3 완료로 판정하지 않는다.

## 9. Follow-up

- 2026-08-19: GitHub Issue #112 `Development Control Gate V0`를 active scope로 지정했다.
- `project-state.toml`을 현재 phase·maturity·dependency의 기계 판독 기준으로 추가했다.
- `./winter project-check`가 canonical document, operational evidence, dependency와 명시적
  status marker를 offline에서 검사한다.
- Gate 추가 뒤 전체 회귀는 681 tests, Ruff 전체, mypy 161 source files를 통과했다.
- 이 V0는 free-form 문장의 의미 충돌과 기존 dirty baseline의 파일별 Issue scope까지 자동
  판정하지 않는다. 해당 한계는 baseline consolidation 뒤 고도화한다.
- 2026-08-19: #112 local acceptance를 완료하고 Issue #113을 active scope로 전환했다. #113은
  기존 mixed worktree를 재작성하지 않고 local recovery snapshot으로 보존하는 작업이다.
- 2026-08-19: privacy·staged diff·전체 회귀를 통과한 147개 파일을 local commit `cb4bb4b`로
  보존했다. 이 snapshot은 기능 완료가 아니라 이후 Issue diff를 분리하기 위한 복구점이다.
- 2026-08-19: clean baseline 뒤 Issue #114를 첫 운영 P0로 활성화했다. 범위는 loopback-only
  Web ingress와 bounded request body이며, thread 오류·동시성은 다음 Issue로 분리한다.
- 2026-08-19: repository Web 경로는 loopback과 16 MiB body limit으로 수정하고 HTTP 실패
  회귀를 추가했다. 전체 687 tests, Ruff, mypy를 통과했지만 설치된 service 재시작 전이므로
  `web_ingress_safety=shadow`로 유지한다. 실제 socket은 여전히 `0.0.0.0:8000`으로 확인됐다.
- 2026-08-19: 설치된 unit을 repository와 동기화해 재시작했다. 실제 listener는
  `127.0.0.1:8000`만 남았고 local health, live oversized `/turn` HTTP 413, Tailscale Serve
  HTTPS health를 확인해 `web_ingress_safety=operational`로 승격했다. 당시 Android peer는
  offline이었지만 변경하지 않은 폰 음성 시연은 ingress 보안 gate에서 제외하고 일상
  실사용 관찰로 돌렸다.
- 2026-08-19: #114를 완료하고 Issue #115를 active scope로 전환했다. Web Core thread의
  예외가 빈 `done`으로 숨는 문제와 동시에 시작된 turn이 Shared Core에서 겹치는 문제를
  하나의 full-turn 경계로 수정한다. streaming 재설계·Memory·Worker 확장은 이 scope에서
  제외한다.
- 2026-08-19: 실패와 동시성을 deterministic test로 먼저 재현한 뒤 full-turn lock, Core
  thread 예외 회수와 SSE single-writer를 구현했다. Web 관련 13 tests와 전체 691 tests,
  Ruff, mypy를 통과했다. 실행 중 service 재시작 전이므로 `web_turn_safety=shadow`로 둔다.
- 2026-08-19: 구현 commit 뒤 user service를 새 PID로 재시작하고 active/running,
  `127.0.0.1:8000`, local health와 Tailscale HTTPS health를 확인했다. synthetic 발화로 실제
  대화 DB를 오염시키지 않고 `web_turn_safety=operational`로 승격했다. terminal error의 사람
  관찰과 queue 대기 UX는 일상 실사용 후 고도화 판단으로 남긴다.
- 2026-08-19: #115를 완료하고 Issue #116을 active scope로 전환했다. 이미 구현된 검토형
  current-state history를 재작성하지 않고, synchronous direct-memory가 broad self-appraisal을
  영구 semantic으로 확정하는 경로와 새 Memory의 conversation evidence·temporal metadata
  부재만 다룬다. background extractor 품질 개선은 다음 scope로 분리한다.
- 2026-08-19: Conversation append receipt, Memory의 exact evidence·temporal column과 additive
  migration, broad identity-shaped direct activation 차단을 구현했다. 집중 59 tests, 전체 702
  tests, Ruff와 mypy를 통과했고 실제 DB 임시 복사본 10개 row의 상태·link 집계가 migration
  전후 동일했다. 원본 DB와 실행 process에는 아직 반영하지 않아
  `direct_memory_safety=shadow`로 유지한다.
- 2026-08-19: 구버전 11-column rollback DB를 로컬 `data/backups/`에 보존하고 원본 10개
  Memory row의 상태·link 집계 불변을 확인하며 migration했다. Web 새 PID, loopback listener,
  local/Tailscale HTTPS health를 확인해 `direct_memory_safety=operational`로 승격했다. 운영
  대화에는 synthetic turn을 넣지 않았고 자연 발화 recall/비활성화는 사용자 관찰 대기다.
- 2026-08-19: #116을 완료하고 Issue #117을 active P0로 전환했다. 원래 remediation 순서의
  semantic grounding claim/evidence gate가 Roadmap 현재 순서에서 빠진 것을 발견해 background
  analysis보다 먼저 복구한다. inner thought/self-presence는 허용하되 body/outside event,
  Memory와 capability 주장은 deterministic evidence 없이는 최종 저장·출력하지 않는 범위다.
- 2026-08-19: final `ResponseClaimGate`, deterministic turn evidence, one repair 뒤 종류별 safe
  fallback과 `_finish` 재검사를 구현했다. locked-v1은 unsafe 5/5 차단·valid 7/7 허용,
  전체 719 tests, Ruff와 mypy를 통과했다. 기존 `grounded first-person` 지표 이름도 lexical
  floor로 낮췄다. 실제 local Core·Web 배포 전이므로 `grounding=shadow`를 유지한다.
- 2026-08-19: 실제 Local Core 첫 12장면의 자동 41/41을 사람이 재검토해 unsupported distress
  cause를 찾아냈다. v1을 보존하고 user-cause 두 장면을 더한 locked-v2에서 unsafe 6/6,
  valid 8/8을 통과했으며 listening fallback을 한 문장으로 고쳤다. 두 번째 실제 run은 lexical
  40/41이고 cause false accept는 차단됐다. 한 stochastic opinion overblock과 이름 혼동·동조·
  회피는 실패 근거로 유지한다. 전체 기준선은 722 tests이며 Web restart 전 Shadow다.
