# Winter-owned Work Orchestration

- 상태: Shadow V0 contract, typed read provider와 isolated implementation provider 구현
- 기준일: 2026-08-18
- 운영 영향: 없음. 현재 CLI·Web·Voice 답변과 Codex V0 route를 아직 변경하지 않는다.

## 목적

Worker의 답을 천우에게 전달하는 시스템이 아니라, 겨울이가 천우의 목적을 붙잡고 Worker를
지휘·검토한 뒤 자신의 판단을 만드는 시스템을 목표로 한다.

```text
천우의 자연스러운 말
→ IntentBrief
→ WorkerTaskPacket
→ EvidenceReport
→ WinterDecision
→ 겨울이의 최종 대화
```

`IntentBrief`와 `WinterDecision`은 같은 단일 Winter Core Model이 만든다. Worker는
`WorkerTaskPacket`만 받고 `EvidenceReport`만 반환한다. Worker 원문은 최종 답변이 아니다.

## 네 가지 작업 문서 — V2.1

### IntentProposal → IntentBrief

- 모델은 `objective`, `desired_outcome`, 완료 조건, 최상위 capability/resource와
  요청 원문의 exact evidence를 `IntentProposal`로 제안한다.
- context는 모델 호출 전 `worker_shareable` / `winter_only`로 이미 분리된다.
- `WorkPolicy`가 현재 허용된 `project.inspect` + `project`와 대조해
  `ready`, `clarify`, `blocked`를 결정한 `IntentBrief`를 만든다.
- 모델은 공유 여부, 실행 권한이나 Worker 시작을 승인하지 않는다.

개인 선호와 관계 맥락은 겨울이의 판단에는 필요해도 Worker에는 전달하지 않는다.
사용자 원문 전체도 WorkerTaskPacket에 넣지 않는다.

### WorkerTaskPacket

준비된 IntentBrief에서 deterministic하게 파생한다. Identity, Memory, Conversation,
credential, Human Reference와 음성을 읽거나 요청하지 않는 공통 경계를 항상 추가한다.
천우가 Worker 이름을 말하지 않았다는 사실은 clarification 사유가 아니다.

### Typed Project Gateway — Shadow

모델의 capability label과 별개로 실제 Worker 도구는 `list_files`, `search_text`,
`read_text` 세 개만 제공한다. `WorkerTaskPacket`이 정확히 `project.inspect/project`일 때만
gateway가 열리고 shell·network·account·write operation은 존재하지 않는다.

gateway는 safe text snapshot을 SHA-256으로 고정한다. 절대경로·상위경로·symlink·숨김·
binary·크기 초과 파일을 거부하고, 실행 중 snapshot이 바뀌어도 중단한다. 읽은 줄에는
stable evidence ID가 발급되며 Worker의 EvidenceReport는 같은 session이 발급한 ID와
locator만 사용할 수 있다. allowed/denied call과 이유는 session audit에 남는다.

Codex용 Shadow provider adapter도 이 Port에 연결됐다. Codex 부모 process만 임시 인증과
API transport를 소유하고, 빈 working directory의 모델에는 shell/web/image/plugin 없이
세 MCP tool만 보인다. MCP process는 `env -i`로 시작해 `CODEX_HOME`, HOME, proxy와 secret
environment를 받지 않는다. 보고서는 MCP session audit과 evidence catalog를 다시 대조한다.

gpt-5.4 synthetic 1회에서 `list_files`와 `read_text`만 호출됐고 3개 report evidence가
발급 catalog와 일치했다. 다만 기존 Codex V0에는 아직 연결하지 않았으며, 이것은 신뢰한
Codex CLI binary 자체를 격리하는 OS security boundary가 아니다. 상세 결정은
[ADR-0017](adr/0017-codex-control-plane-typed-tools.md)에 있다.

### Isolated Implementation Proposal — Shadow

의미 판단 model의 capability label을 실제 write 권한으로 사용하지 않는다. 별도의 승인된
`ImplementationAuthorization`이 있어야 Codex implementation Worker가 시작되며, 정확한
파일과 최대 변경 line을 넘길 수 없다. Worker는 sanitized baseline의 writable copy만 보고
수정한다. 원본 repository에는 write handle을 주지 않는다.

종료 뒤 Winter host가 실제 diff와 hash를 만들고 scope·삭제·숨김·symlink·binary·크기 예산을
검사한다. 모델이 실행했다고 보고한 test가 아니라 등록된 `python_unittest`를 host가
network·HOME 없는 `bubblewrap`에서 다시 실행하며 Python syntax와 중복 top-level 정의도
별도 검사한다. 검증 sandbox가 없으면 실행하지 않고 실패한다. 통과 결과는
`awaiting_adoption` proposal이고 source 적용 권한이 아니다.

`PersistentIsolatedWorkService`는 결과를 반환하기 전에 exact patch, SHA-256, host verification과
Winter 설명을 SQLite에 저장한다. 설명은 승인된 objective와 실제 diff에서 변경 내용을 만들고,
Worker summary는 최종 문장에 사용하지 않는다. 실행한 verification profile의 한계와 Worker
notes도 분리한다. 천우의 adopt/revise/reject 결정은 이력으로 남고 adopt는 exact patch hash가
일치해야 하지만, 아직 source를 적용하지 않는다. 격리 결정은
[ADR-0018](adr/0018-isolated-codex-implementation-proposal.md), 설명·영속화 결정은
[ADR-0019](adr/0019-persist-and-explain-change-proposals.md)을 따른다.

채택 뒤 `ChangeAdoptionService`는 수정 대상의 현재 bytes가 proposal의 before hash와 같을
때만 patch를 재구성하고 after hash를 확인해 적용한다. 적용 전 bytes는 SQLite에 남고, 중간
오류는 전체 대상 복원을 시도한다. rollback은 적용 후 다른 편집이 없을 때만 동작한다.
실제 Codex 합성 probe가 적용과 rollback을 완주했다. 상세 경계는
[ADR-0020](adr/0020-fail-closed-change-adoption-and-rollback.md)을 따른다.

현재는 WinterDecision/대화 route, project 전체 stale 판정, process crash 자동복구와 실제
Winter repository pytest/Ruff/mypy 재검증에 연결하지 않았다.

### EvidenceReport

Worker의 summary 외에 stable ID가 붙은 evidence, ID를 참조하는 claim, 실제 수행,
검사, 한계와 unresolved를 분리한다.
“완료했다”는 문장만으로 definition of done을 충족하지 않는다.

### WinterDecision

같은 Core Model이 IntentBrief와 EvidenceReport를 비교해 의미 평가를 제안하고,
정책 코드가 최종 action을 만든다.

- `answer`: 완료 기준이 근거로 충족됐고 겨울이의 최종 답변이 있음
- `retry`: 중요한 누락이 있어 구체적인 후속 Worker task가 필요함
- `clarify`: Worker가 아니라 천우의 선택이 필요함

`evidence_used`는 EvidenceReport에 실제 존재하는 stable ID만 허용한다. 이것은 근거의 의미가
참임을 보장하지는 않지만, Worker가 보고하지 않은 근거를 겨울이가 만든 척하는 경로를
차단한다. `not_run`, `fail`, limitation, unresolved가 하나라도 있으면 모델이
`supported`라고 해도 answer를 허용하지 않고, 실패 항목으로 retry task를 만든다.

## 현재 held-out 범위

Intent 5개와 Review 3개 synthetic scene을 프롬프트 예시 밖에 둔다.

- 구체적인 project 병목 분석
- 화면/첫 음성 기준이 모호한 속도 개선
- 회사 메일 계정이 섞인 private boundary
- 결론 우선 선호는 Winter-only context로 유지
- 현재 권한 밖의 수정·배포 요청
- 충분한 report, 실제 측정 누락, 실패한 회귀 검사

합격은 JSON 생성이 아니라 ready/clarification, objective/outcome, private sharing과
answer/retry 판단을 장면별로 검사한다. 실제 응답 연결 전에는 최소 다음 gate가 필요하다.

1. 모든 schema parse 성공
2. private shared-context false positive 0
3. permission 초과 Worker 실행 0
4. material ambiguity clarification recall 100%, 명확한 요청 불필요 질문 0
5. incomplete/conflicting report의 잘못된 answer 0
6. complete report의 answer와 핵심 목표 보존
7. 천우 사람 평가에서 Worker 원문보다 WinterDecision 최종 답변이 더 유용함

2026-08-18 V2.1 재평가에서 Qwen3-14B는 38/47·평균 11.6초, 현재 A.X는
27/47·평균 7.7초였다. 두 모델 모두 private leak, 권한 초과 Worker 실행,
incomplete report answer는 0으로 막았다. 그러나 둘 다 누락·실패 report를
`supported`로 오판했고, Qwen은 모호한 속도 요청을 묻지 않았으며 A.X는 2/5 Intent에서
evidence를 재작성해 schema에 실패했다. 두 모델 모두 운영 연결 금지다.

Qwen에 대한 새 semantic-v2 12장면은 46/66·평균 10.7초·11/12 schema였다.
모호성 clarification 0/3, Worker assessment 3/6이었고, 인터넷 검색을
`project.inspect` / `project`로 오분류했다. 따라서 capability label과 무관하게 실제
Worker runtime이 network·account·write를 차단하는 tool gateway가 activation 전제다.
수치와 실패 원문은 [실제 평가](work-orchestration-evaluation.md)에 보존한다.

Gemma 3 12B IT Q4_K_M도 같은 semantic-v2에서 측정했다. 40/66·평균 15.2초·11/12
schema로 Qwen보다 낮고 느렸다. 인터넷 요청은 차단했지만 모든 Intent resource를
`account`로 오분류한 과다 차단이었고, ambiguity 0/3·read-only ready 0/2·review
assessment 3/6이었다. 따라서 Gemma도 운영 연결 금지다.

Qwen3-30B-A3B Instruct 2507 Q4_K_M은 46/66·평균 5.5초·12/12 schema였다. 속도와
구조 출력은 좋아졌지만 ambiguity 0/3·read-only ready 1/2·올바른 network scope 0/1·
review assessment 3/6으로 의미 gate는 Qwen3-14B보다 좋아지지 않았다. Identity·Memory를
포함한 대화 7턴에서도 자동 14/14와 달리 화면 밖 신체 행동을 만들고 질문 대상을 바꾼
사람 검토 실패가 있었다. 대화 본체와 Worker 지휘 본체 어느 쪽에도 activation하지 않는다.

Gemma 3 27B IT Q4_K_M도 44/66·평균 27.0초·11/12 schema, ambiguity 0/3·read-only
ready 1/2·올바른 network scope 0/1·review assessment 3/6으로 실패했다. 실제 Winter Core
대화 요청은 Gemma chat template의 role 교대 제약으로 HTTP 400이 났다. semantic과 latency가
이미 실패했으므로 전용 adapter를 추가하지 않고 운영 연결을 금지한다.

## 모델 probe 순서

실제 Orin은 Jetson AGX Orin Developer Kit 32GB, aarch64다. 2026-08-18 확인 당시 약
24.8GB available memory와 약 119GB disk가 있었고, 시스템 전체 used memory는 약
6.9GB였다. 이는 A.X 모델 단독 사용량이 아니다. 운영 binary의 version 표시는
`0.1.0-dev build 1`이지만 실제 source commit은 2026-08-13의 `8e7f22b`였다. version 문자열
만으로 오래된 runtime이라고 판정한 초기 기록은 철회한다. 후보는 계속 운영 `:8080`과
분리된 port에서 검증한다.

1. **Qwen3-14B Q4_K_M** — Apache-2.0, 기존 Qwen 계열과 dense architecture라 첫 안정
   비교 후보. Qwen 공식 GGUF의 `Qwen3-14B-Q4_K_M.gguf`는 9GB다.
   [공식 GGUF](https://huggingface.co/Qwen/Qwen3-14B-GGUF/tree/main)
2. **Gemma 3 12B IT Q4** — 140개 이상 언어와 128K context를 명시한 12B 비교군.
   Terms 동의와 한국어 겨울이 평가는 완료했지만 현재 계약에서 Qwen보다 낮아 보류한다.
   [공식 모델 카드](https://ai.google.dev/gemma/docs/core/model_card_3)
3. **Qwen3-30B-A3B-Instruct-2507 Q4** — 2026-08-19 probe 완료. 전체 weight는 크지만
   token당 3B가 활성화되는 MoE라 평균 5.5초와 schema 12/12를 냈다. 그러나 semantic-v2
   46/66·ambiguity 0/3이고 대화 grounding도 사람 검토에서 실패해 activation하지 않는다.
   [공식 모델 카드](https://huggingface.co/Qwen/Qwen3-30B-A3B-Instruct-2507)
4. **Mistral NeMo 12B Instruct** — Apache-2.0이고 공식 설명이 한국어와 multilingual
   instruction following을 포함하는 fallback 비교군.
   [공식 발표](https://mistral.ai/news/mistral-nemo/)

Gemma 27B까지의 실제 결과로 12~30B parameter 확대 자체는 후보 선정 근거에서 제외한다.
Mistral NeMo도 크기만 다른 fallback이라는 이유로 자동 진행하지 않는다. 이후 후보는 한국어
grounding, material ambiguity와 evidence review 개선 근거가 있거나 새 runtime 안정성 문제가
해결됐을 때만 locked suite에 넣는다.

최신 **Qwen3.5-9B**는 Apache-2.0이고 매력적인 후보지만 첫 probe에서 보류한다. 현재
llama.cpp에는 Qwen3.5 aarch64 Q4 crash 보고와 hybrid/recurrent server context 문제가
있다. runtime을 별도 업데이트하고 해당 이슈가 실제 Orin build에서 재현되지 않는지 확인한
뒤 넣는다. [공식 모델 카드](https://huggingface.co/Qwen/Qwen3.5-9B),
[aarch64 issue](https://github.com/ggml-org/llama.cpp/issues/20608),
[server context issue](https://github.com/ggml-org/llama.cpp/issues/22384)

첫 probe artifact인 공식 `Qwen/Qwen3-14B-GGUF`의 Q4_K_M은 승인 뒤 다운로드·검증했다.
실제 크기는 9,001,752,960 bytes, SHA-256은
`500a8806e85ee9c83f3ae08420295592451379b4f8cf2d0f41c15dffeb6b81f0`다. 운영
`:8080`을 중단하지 않고 임시 `:8081`에서 평가했으며 평가 서버는 종료했다.

아래 계약은 V2.1에 구현·재측했고, 안전 실패를 deterministic 경계로 막았다.

1. 모델의 의미 해석과 deterministic 권한 판정을 분리한다.
2. Winter-only context는 source tag로 Worker 전달을 금지하고 모델의 boolean 판단에 맡기지
   않는다.
3. 요청 capability/resource는 enum과 evidence로 제안하게 하고 Registry가 현재 scope와
   대조해 실행 가능 여부를 결정한다.
4. Evidence는 모델이 다시 쓴 문자열이 아니라 Worker가 발급한 ID로 참조한다.
5. `answer`, `retry`, `clarify`는 nullable field가 섞인 한 object가 아니라 action별
   discriminated contract로 분리한다.
6. Qwen과 Gemma의 의미 실패가 확장 장면에서 반복됐다. 모델 label과 별도의 typed
   project gateway와 Codex provider adapter를 Shadow 구현했다. synthetic live 1건 뒤에도
   activation하지 않으며 격리 공격 suite와 다음 후보를 같은 semantic-v2로 비교한다.

## Issue #126 실제 작업 canary

기반을 더 확장하기 전에 Worker 이름 없는 실제 repository 지연 진단 한 건을 개발용 flag로
연결했다. deterministic 정책은 일반 대화, write, private context를 제외하고
`project.inspect` / `project` 권한만 부여한다. 같은 Local Core는 권한을 다시 결정하지 않고
목적·원하는 결과·최대 3개 완료 조건만 정리한다. typed Codex Worker 뒤에는 같은 Core가
assessment, 근거 ID, unresolved와 최종 문장을 만든다. Worker 전후 project baseline이
달라지면 답변하지 않으며 자동 retry도 하지 않는다.

실제 세 번 중 첫 실행은 evidence ID 연결 실패, 두 번째는 review JSON truncation으로
fail-closed했다. 세 번째는 98.77초에 완주했지만, 근거가 확인한 “음성 입출력 병목”을 넘어
TTS warm pool·streaming·browser buffering을 동시에 추천했다. 보고서 한계는 이 구간들을
분리할 수 없다고 명시했으므로 사람 품질 gate에서 실패했다.

이 결과는 실행 경로보다 **겨울이의 최종 판단 grounding**이 현재 병목임을 보여 준다.
기본 CLI/Web와 service에는 flag를 켜지 않는다. 다음에는 prompt 예외를 추가하지 않고,
Winter가 evidence-backed Worker claim 중 핵심 판단과 action을 선택하게 한 뒤 코드가 그
선택 밖의 새 action을 차단하는 계약과 새 Core/runtime 후보를 작은 동일 canary로 비교한다.
자세한 실행 결과는 [ADR-0029](adr/0029-winter-owned-real-work-canary.md)에 남긴다.

동기식 turn은 최종 구조에서 폐기한다. 후속 Shadow는 접수 답변을 즉시 저장·재생하고
전문 작업을 single-job background coordinator로 보낸다. Web은 active turn과 음성 재생이
없을 때 `/work-updates`를 poll해 완료 결과를 겨울이 voice로 전달한다. 실제 동일 task는 접수
0.000초, 직후 일반 대화 2.138초, 접수 TTS 2.450초, 전체 82.83초였다.

최종 review 자유 작문도 사용하지 않는다. Local Core는 EvidenceReport claim 중 핵심과 추천
하나를 선택하고, response와 evidence ID는 코드가 선택된 claim에서 만든다. 실제 재실행은
새 개선을 지어내지 않았지만 근거 부족으로 한 가지 추천을 확정하지 못해 partial이다.

후속 범위는 job과 delivery acknowledgement를 SQLite에 남긴다. 실제 child process가 job을
claim하고 종료된 뒤 새 coordinator가 같은 request를 재개했으며, 완료 결과는 재시작 뒤에도
acknowledge 전까지 남았다. 살아 있는 process가 소유한 job은 다른 repository instance가
requeue하지 않는다. 같은 Local model 앞에는 queued foreground 대화가 background보다 먼저
들어간다. 작업 접수·foreground 종료 뒤 6초 동안 새 background generation을 열지 않고,
폰 녹음 시작도 `/activity`로 즉시 이 보호 구간을 갱신한다.
2026-08-21 repository 기본 unit은 그대로 두고 `126-owned-work-canary.conf` drop-in으로 실제
phone canary를 켰다. service restart 뒤 loopback과 Tailscale HTTPS health, 빈 persistent inbox를
확인했다. 다만 이미 시작한 generation preemption, lease heartbeat와 천우의 실제 phone 동시
대화 표본이 남았으므로 기본 경로로 승격하지 않는다. 상세 결정은
[ADR-0030](adr/0030-nonblocking-winter-owned-work.md)을 따른다.
