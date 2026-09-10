# Text-first Winter 구현 TODO

- 작성일: 2026-09-10
- 상태: T0/T1/T2 (#128~#132) 작업 범위 완료, T2 후보 품질 gate 실패·legacy 유지. 다음 active T3.1 #133은 미착수다.
- 설계: [Text-first 모듈 설계](text-first-refactoring-design.md).
- baseline: `0c4f557`, `feat/126-winter-owned-work-canary`.
- T번호는 계획상의 ID다. Epic #127, T0 #128, T1.1 #129, T1.2 #130, T2.1 #131, T2.2 #132, T3.1 #133을 등록했다.
- 기록 보존과 재사용: [기록 인계표](text-first-records-handoff.md).
- 기존 설계의 T0~T6 순서를 유지하고, 각 단계 안에서 작은 Issue로 나눌 단위를 표시한다.

## 작업 원칙

- 완료한 작은 Issue마다 목적, 변경, 검사 결과, 사용자에게 가능해진 동작을 보고한다.
- 첫 구조 추출에서는 모델, prompt 정책과 DB schema를 동시에 바꾸지 않는다.
- 실제 정체성·기억·Reference 원본은 그대로 보존하고 테스트는 격리 fixture DB로 한다.
- 기본 검증은 Docker dev에서 수행한다. 모델 서버 연결과 외부 provider 검증은 별도다.
- Voice 고도화는 보류한다. 기존 인터페이스 계약 테스트는 유지하지만 마이크·TTS 청취를
  새 텍스트 작업의 완료 조건으로 두지 않는다.
- 코드량·테스트 개수·Reference 문구 유사도는 대화 자연스러움의 대체 지표가 아니다.
- 실제 사용용 CLI는 각 단계 후에도 실행 가능해야 한다. 폴더 전체 재배치를 먼저 하지 않는다.

## 전체 순서

```text
T0 작업 기준 전환
 → T1 기존 동작을 보존한 모듈 추출
 → T2 실제 텍스트 대화 정책 비교
 → T3 선택적 기억 검색과 context 예산
 → T4 기억 갱신·정정·재시작
 → T5 기존 Codex의 text 작업 경험 통합
 → T6 다른 provider와 연속 사용 검증
```

T2에서 실제 겨울이와 대화를 시작한다. 기억·Agent를 모두 만든 뒤에야 대화 품질을 보는
순서가 아니다. T2의 모델 품질 실패는 기록하고 후보를 기본값으로 승격하지 않는다. 저장·검색
리팩터링처럼 독립적으로 유용한 후속 작업은 기존 안정 정책 위에서 진행할 수 있다.

## T0 — 최신 목표를 프로젝트 기준으로 등록

- [x] T0.1 Text-first 목적의 상위 Issue와 첫 구현 하위 Issue를 만든다 (#127/#128/#129).
- [x] T0.2 기존 #126을 삭제하지 않고 partial 결과와 보류 이유를 연결한다.
  [보류/계승 기록](https://github.com/WK-Hanelso/winter-ai/issues/126#issuecomment-5611657530)
- [x] T0.3 `project-state.toml`, AGENTS, README, V1 목표, Roadmap, Maturity의 현재 우선순위를
  동기화한다. 새로운 기능의 성숙도를 미리 올리지 않는다.
- [x] T0.4 현재 커밋과 작업 트리를 보존하고 `chore/128-text-first-transition` branch를 만든다.

포함할 세 목표: 동일한 겨울이의 지속성, 실제 외부 Agent 활용, 관계가 느껴지는 텍스트 대화.
Voice와 자기 코드 변경의 전체 lifecycle은 후순위로 기록한다. `project-check`의 자유 서술
검사 한계를 고려해 문서 의미도 대조한다. 이 단계에는 모델/서비스 변경이 없다.

**완료:** 후속 Agent도 최신 목표와 단일 active Issue를 읽을 수 있고 project-check가 통과한다.

2026-09-10 T0 확인: parent baseline 보존, #127의 실제 sub-issue #128/#129 연결,
#126 open/partial 기록, 새 contract와 기존 20개 maturity 보존, 문서/diff/project-check 검증.
실제 코드·DB·서비스는 변경하지 않았다. 다음 제어 scope를 #129로 넘기되 코드 구현은 시작하지 않았다.

## T1 — 동작을 유지하면서 교체 지점 만들기

### T1.1 공용 초기화 코드 추출

- [x] `src/companion/application/bootstrap.py`를 추가한다.
- [x] CLI `run()`에 모인 model/repository/정책 생성 코드를 text runtime factory로 옮긴다.
- [x] 기존 `user_cli` 옵션과 Python config를 같은 factory 입력으로 변환한다.
- [x] 모델·DB의 생성 및 종료 책임을 runtime 한 곳에 모은다.

기존 `ChatModel`, repository와 `CompanionCore`를 그대로 조립한다. 첫 입력은 config와
필요한 Port 구현체이고, 출력은 `core`, 선택적인 task inbox, 종료 함수를 가진 runtime이다.
실험용 fake는 명시적 옵션으로만 허용한다. Voice/Web 조립 코드는 한 번에 옮기지 않는다.

**검사:** text startup에 음성 장비·모델이 불필요, 기존 옵션과 DB 경로 유지, 잘못된 설정은
명시적 실패. 기존 CLI 테스트와 startup 계약 테스트를 사용한다.

**2026-09-10 #129 완료:** 새 startup 계약 27개를 포함한 Docker offline 847 passed
(39.96초), `ruff check .` 통과, `mypy` 176 source files 통과. 기존 CLI 23개도 별도 통과했다.
Identity/기억/evidence 재시작, 저장소별 실패, 명시적 Worker opt-in, 모델 없는 조회와 fake
fallback 차단을 검증했다. 최초 음성 의존성 테스트는 공유 prosody metadata를 음성 엔진으로
잘못 분류해 수정했고, 실제 STT/TTS/device import 차단 검사는 유지했다.
운영 데이터와 schema, Core 정책, Voice/Web/service를 변경하지 않았다.
종료 함수는 기존 job을 취소하지 않는 bounded drain이다. 실제 모델·사용자 체감은 미검증이다.
[ADR 0032](adr/0032-text-runtime-bootstrap.md)를 따른다. 다음은 #130이며 코드 미착수다.

### T1.2 정체성 snapshot과 문맥 조립 추출

- [x] 기존 Identity/Belief/Capability/Open Loop를 읽는 `CharacterSnapshot`을 정의한다.
- [x] `src/companion/dialogue/context_assembler.py`로 prompt 조립을 추출한다.
- [x] Core가 `snapshot + 현재 입력 + 최근 대화 + 검색 근거`를 assembler에 전달하게 한다.
- [x] 기존 system message 우선순위와 실패 경로를 유지한다.

새 관계 DB를 만드는 작업이 아니다. snapshot은 기존 상태의 read-only view다. 이전 public
import 경로를 유지하고 `memory.py → memory/` 같은 이름 변경은 하지 않는다.

**검사:** spy/fake model로 전달 입력을 확인하고, identity와 evidence 보존, 결과 저장,
모델 실패·재시작을 확인한다. 실제 모델의 확률적 문장이 byte 단위로 같아야 한다고 요구하지 않는다.

**T1 완료 경험:** 지금처럼 CLI 대화를 할 수 있으며, 문맥 구성만 독립적으로 고칠 수 있다.
이 단계의 DB migration은 없다. 이전 코드로 바로 되돌릴 수 있다.

**2026-09-10 #130 완료:** 수정 전 `393bc4c`의 synthetic ChatRequest 6종을 먼저 고정한 뒤
분리 후 같은 지문임을 확인했다. 새 계약 14개를 포함해 Docker offline 861 passed (38.82초),
`ruff check .` 통과, `mypy` 179 source files 통과. 11개 system block 순서, 스냅샷 불변성,
evidence/버전, 다음 turn 상태 반영, repair 재사용과 오류 전파를 검증했다. 기존 bootstrap의
임시 SQLite 재시작 검사와 Shared Core 회귀도 통과했다.
초기 테스트의 timeline ID assertion은 기존 renderer가 ID를 출력하지 않는 사실에 맞춰
내용 assertion으로 정정했고, optional grounding 지침 타입 누락도 보완했다. 요청 기준선은
재생성하지 않았다. 운영 데이터/서비스·모델·policy는 변경하지 않았고 실제 대화 체감은 미검증이다.
[ADR 0033](adr/0033-character-snapshot-context-assembler.md)를 따른다. 다음 #131은 코드 미착수다.

## T2 — 같은 모델에서 자연스러운 대화 비교

### T2.1 평가 장면과 정책 선택 경계

- [x] 기존 Director/VerbalStyle 조합을 `LegacyDialoguePolicy`로 감싼다.
- [x] 같은 입력 계약을 받는 `ContextualDialoguePolicy` 후보를 별도 구현한다.
- [x] Python config에서 policy를 명시적으로 선택하게 한다. 오류 시 몰래 다른 정책을 쓰지 않는다.
- [x] 짧은 multi-turn 개발 장면과 미노출 평가 장면을 먼저 고정한다.

후보 정책 입력은 현재 문장뿐 아니라 최근 turn, 직접 합의된 대화 선호, 관련 기억이다.
출력은 모델에 줄 짧은 대화 지침이다. 기본 생성은 한 번이며, 선행 감정 JSON 추론이나
후행 말투 rewrite를 매번 강제하지 않는다. 광범위한 regex override와 무작위 존댓말 분포를
후보의 기본 선택 기준에서 빼는 효과를 각각 분리해서 비교한다.

**2026-09-10 #131 완료:** `legacy`, `contextual`, `contextual-moves-only`,
`stable-register-only` 선택 경계를 만들었다. 기본 legacy는 유지하고 후보 선택은 CLI에서
실험으로 표시한다. 후보 자체의 모델/저장소 호출은 없으며 감정 판단은 동일 LLM의 문맥
해석 가설이다. 기존 act/길이/검색·claim safety·Worker opt-in은 유지한다.
후보 구현 전 10개 장면(development 5/reserved 5)을 고정했다. reserved는 개발 입력에서
제외했지만 작성자는 보았으므로 독립 blind benchmark가 아니다. 실제 모델 평가는 미착수다.
새 계약 28개 포함 Docker offline 889 passed (40.35초), Ruff 통과, mypy 180 source files 통과.
기존 6종 요청 기준선도 일치한다. 일반 생성 1회, 안전 위반 시 기존 repair 추가를 검증했다.
말투 label별 repair가 후보에 적용되지 않는 변화와 `register=contextual` metadata의 한계는
[ADR 0034](adr/0034-dialogue-policy-candidate.md)에 기록했다. 다음 #132의 격리된 실제 A/B는
[평가 계획](text-dialogue-evaluation.md)을 따른다. 모델/개인 DB/서비스는 변경하지 않았다.

### T2.2 대화 품질 비교와 기본값 결정

- [x] 동일 모델/config/context에서 baseline과 후보를 실행한다.
- [x] 공감+해결 복합 요청, 농담→정정, 자기 의견, 기억 불명확성, 대화 종료 장면을 비교한다.
- [x] 응답의 intent 충족, 근거 없는 과거/능력, 불필요한 질문, 상투 표현, register 변화를 기록한다.
- [x] 천우가 별도 text session에서 후보와 대화할 수 있게 사용 명령을 문서화한다 (현재 기본 터널 복구 필요).

검사는 정답 문장 암기가 아니라 행동 기준이다. 예를 들어 "답답한데 해결법을 설명해줘"는
공감만 하고 설명을 빠뜨리면 실패다. 근거 없는 활동·관계 설정과 capability 주장을 막는
기존 claim gate를 해제하지 않는다. false block과 repair율도 함께 기록한다.

**T2 완료 경험:** 모델을 바꾸기 전에도 정책 변경의 실제 차이를 CLI에서 비교할 수 있다.
선호한 후보라도 기존 안전 계약 회귀가 있으면 기본값으로 승격하지 않는다. 모델 능력이
병목이면 실패 장면을 다음 모델/학습 검토의 입력으로 남긴다.

**2026-09-10 #132 평가 범위 완료 / 품질 gate 실패:** 실제 A.X-4.0-Light Q4_K_M,
합성 Identity·임시 SQLite로 주 분석 120턴/133회 모델 호출을 기록했다. 겹쳤던 초기 배치
60턴은 별도 보존하고 순차 재실행본을 사용했다. 후보는 중간 수정하지 않았다.
기본 legacy 유지, 사용자/독립 사람 평가는 pending이며 Codex 출력 분석과 구분한다.
설명 일부 개선, 기억 환각/주어 변경, 의견 회피, 목록 중간 잘림, 검사 오탐/누락을
[결과/원문](evaluations/132-text-policy/README.md)에 남겼다. 실제 기억 유실 시험은 아니다.
Docker offline 898 passed (40.69초), Ruff 통과, mypy 181 source files 통과.
Orin 모델은 정상이지만 기본 Host 터널이 없어 임시 18082로 평가했고 종료 후 닫았다.
서비스·개인 DB·Core 정책은 변경하지 않았다. 다음 #133은 독립적인 bounded 조회다.

## T3 — 모든 기록을 읽지 않고 기억 찾아오기

### T3.1 bounded repository 조회

- [ ] `ConversationRepository`에 최근 기록 조회와 선택 ID/범위 조회 Port를 추가한다.
- [ ] `sqlite_repository.py`에 `ORDER BY id DESC LIMIT ...` 기반 조회를 구현한다.
- [ ] Core의 turn 경로에서 `list_messages()` 전체 조회를 제거한다.
- [ ] 기존 전체 조회는 audit/내보내기 용도로 남긴다.

최근 기록은 실제 시간/ID 순서로 모델에 전달하고, 잘린 대화나 role 순서가 모델의 chat
template 계약을 어기지 않게 한다. 신규 Port는 fake repository도 함께 구현한다.

### T3.2 검색 index와 근거 bundle

- [ ] Docker SQLite의 FTS 지원을 확인하고 대화 원문/기억의 lexical index를 만든다.
- [ ] 새 turn index는 자동 갱신하며, 과거 index 구축은 checkpoint로 재개한다.
- [ ] 현재 질문+최근 주제에서 후보를 찾고 선택된 근거 원문만 읽는다.
- [ ] `EvidenceBundle`에 원문 ID, 필요한 excerpt, 유효 시간과 revision 정보를 담는다.
- [ ] 한국어 다른 표현·대명사·시간 질문의 검색 실패를 따로 측정한다.

FTS는 첫 baseline이다. 대명사는 session 문맥으로 보완하되 모르는 지시 대상을 만들어내지
않는다. 의미가 같고 단어가 다른 장면에서 반복 누락되면 local embedding Port를 별도 Issue로
추가한다. vector DB 도입 자체를 선행 목표로 두지 않는다.

### T3.3 전체 context 예산

- [ ] 정체성/system 지침·최근 대화·검색 결과·작업 상태·출력 여유를 하나의 예산으로 계산한다.
- [ ] 모델 tokenizer와 chat template overhead를 반영하는 token counter 계약을 둔다.
- [ ] 현재 입력과 필수 정체성을 우선 보존하고 낮은 관련도 발췌부터 줄인다.
- [ ] 관련 근거가 예산에서 탈락한 이유를 content 복제 없이 ID로 계측한다.

**검사:** 큰 synthetic history에서도 한 turn이 전체 원문을 읽지 않음, 선택 원문 ID 정확성,
정정 전후 조회, 모름, 긴 입력의 명시적 처리, index 재구축/중단 복구. 검색 후보 recall과
모델의 최종 회상 정확성을 따로 측정한다.

**T3 완료 경험:** 오래된 내용을 질문했을 때 필요한 기록을 찾아 답하고, 길어진 전체 대화를
매번 모델에 넣지 않는다. 모든 질문에서 완벽한 회상을 보장했다는 뜻은 아니다.

## T4 — 기억 갱신과 재시작 후 연속성

### T4.1 durable turn 기록과 maintenance 예약

- [ ] stable turn ID와 처리 상태를 정의하고 기존 conversation evidence ID를 보존한다.
- [ ] 최종 assistant 응답과 분석 예약 outbox를 동일 conversation DB transaction에 기록한다.
- [ ] background consumer가 기존 분석 queue로 전달하고 처리 checkpoint를 저장한다.
- [ ] 다른 DB projection은 idempotency key로 중복 반영을 막고 실패 시 재개한다.

user 메시지는 응답 생성 전에 저장하고 실패한 turn도 식별한다. 답변이 생성되지 않은
turn을 성공 대화로 분석하지 않는다. queue에 들어가기 전에 process가 죽어도 원본으로
미완료 처리를 판별할 수 있어야 한다. 기존 여러 DB를 동시에 commit했다고 가정하지 않는다.

### T4.2 기억의 시간·정정·삭제

- [ ] 기존 memory fields와 새 요구 필드의 migration mapping을 먼저 작성한다.
- [ ] content/kind/evidence/valid time/status/revision/supersedes를 함께 검증한다.
- [ ] 명확한 정정은 새 revision으로 반영하고 애매한 충돌은 candidate로 둔다.
- [ ] 원문·기억 삭제 시 검색 index와 파생 기억/cache를 무효화하고 재추출도 막는다.
- [ ] 기존 DB에서 additive migration, 기존 row 보존, backup/rollback을 검사한다.

기억 추출 모델의 결과는 제안이다. 원문 span, 안정/일시 상태 구분, 확신도와 허용 정책을
검사하며 기존 Shadow 품질 실패를 우회하지 않는다. 원본 보존과 index 업데이트는 분석
내용의 activation 성공 여부와 독립적으로 작동한다.

### T4.3 session 작업 기억

- [ ] 현재 주제, 지시 대상, 미해결 질문과 관련 task ID를 작은 session state로 저장한다.
- [ ] 각 항목에 근거 turn과 revision/유효 범위를 연결한다.
- [ ] 재시작하면 최근 raw turns와 session state를 복원한다.
- [ ] 불확실한 해석은 장기 사실과 구분하고 자연스러운 확인 질문의 입력으로만 사용한다.

**검사:** 기록/queue 전달/분석 반영의 각 crash 지점, 같은 turn 재처리, 저장소 오류,
정정 후 옛 값 재등장, 삭제 후 index 재구축, 재시작 뒤 "아까 그 이야기" 회상.

**T4 완료 경험:** 천우가 별도 정리 명령을 실행하지 않아도 기록과 분석이 이어진다.
자동 활성화하지 못한 추론은 후보로 남으며 기억한 척하지 않는다.

## T5 — 기존 Codex를 대화 속 작업 기능으로 정리

### T5.1 TaskService 추출

- [ ] 기존 `owned_work`와 Codex route를 `TaskService` 경계로 감싼다.
- [ ] `submit / status / ready / acknowledge`를 기존 job/inbox repository로 연결한다.
- [ ] packet 작성, 실행 권한, provider 실행, 결과 검증 책임을 구분한다.
- [ ] Core의 provider별 분기를 서비스 호출로 줄인다.

초기에는 현재 승인된 read-only 범위와 explicit opt-in을 유지한다. 자유로운 자동 위임과
write를 켜는 작업은 아니다. 같은 Local Core Model이 목적 정리와 결과 판단을 맡고,
Worker에는 승인된 최소 기술 자료만 전달한다.

### T5.2 text 입력과 완료 event 동시 처리

- [ ] CLI 입력 event와 Worker 완료 event를 한 session 출력 루프에서 처리한다.
- [ ] 입력 중인 문자열을 보존하면서 접수·완료·오류를 표시한다.
- [ ] Local model foreground 우선권과 기존 durable job recovery를 재사용한다.
- [ ] 재시작 뒤 미전달 결과를 복원하고 delivery ID로 중복을 억제한다.

터미널 line editor의 동작과 외부 provider 실행은 분리해서 검사한다. 출력 직후 ack 전
crash에서는 재전달 가능성을 명시하며 exactly-once를 주장하지 않는다. provider 실행 재개도
읽기 전용 작업에 제한한다. 이미 실행 중인 background generation의 점유 시간은 별도 측정한다.

**검사:** 느린 stub Worker 중 일반 대화, 부분 출력/실패, 재시작 후 결과, scope/개인정보
누출 차단. 이후 승인된 실제 project read-only task 한 건으로 겨울이의 결과 유용성을 평가한다.

**T5 완료 경험:** 천우는 Codex 작업을 맡긴 뒤 다른 이야기를 이어가고 결과를 나중에 받는다.
기존 #126의 partial 판단을 구현 성공만으로 해결됐다고 보고하지 않는다.

## T6 — provider 확장과 여러 세션의 제품 경험 확인

### T6.1 추가 provider 하나

- [ ] Claude 등 사용자 선택 provider의 실제 사용 가능 경로/인증/권한을 확인한다.
- [ ] TaskService의 공통 입력·결과·실패 계약을 구현하는 adapter 하나를 추가한다.
- [ ] timeout·실패·취소 지원 여부를 명시하고 동일한 privacy/scope 검사를 적용한다.
- [ ] 실제 read-only task로 검증한 provider만 capability에 사용 가능하다고 표시한다.

Codex의 GPT 기반 Worker와 Claude를 동시에 지원하는 것은 각각의 adapter 검증이 끝난
뒤의 상태다. provider를 실제로 호출하지 않은 fake 계약 테스트만으로 지원을 주장하지 않는다.

### T6.2 end-to-end 사용 검증

- [ ] 여러 session과 재시작을 포함해 일관성/기억/Agent 작업을 한 흐름으로 평가한다.
- [ ] 다른 표현으로 옛 기억 질문, 명시적 정정, 농담→진지한 전환을 포함한다.
- [ ] 작업 접수 후 대화, 실패 설명, 재시작 후 결과 회상을 포함한다.
- [ ] 30분 자유 대화와 미노출 장면의 사람 평가를 별도로 기록한다.

**완료 경험:** 텍스트에서 겨울이와 관계를 이어가며 실제 Agent의 도움을 받는다. 통과한
범위만 제한 운영으로 표시하고, 확인된 실패가 다음 작은 개선 Issue가 된다.

## 각 Issue의 검증과 보고

코드 변경 시 해당 모듈의 meaningful 계약/실패 테스트부터 실행하고, 아래 기본 회귀를
Docker에서 한 번 수행한다. 문서만 바뀐 Issue는 문서와 project-check 검증으로 마친다.

```bash
./winter project-check
docker compose run --rm dev pytest -m "not model and not voice and not slow"
docker compose run --rm dev ruff check .
docker compose run --rm dev mypy
./winter project-check
```

외부 Worker·모델 검증은 위 오프라인 검사와 분리한다. 합성 DB/장면의 테스트 성공, 실제 모델
성공, 천우의 대화 경험 판정을 구분한다. 모델·정책·context 설정과 코드 revision을 기록해
다음 비교가 같은 조건인지 확인한다.

각 Issue 종료 보고는 아래 다섯 가지를 담는다.

1. 어떤 사용자 경험을 개선하려고 했는가.
2. 어떤 모듈/계약을 바꿨는가.
3. 무엇으로 검증했고 무엇은 아직 미검증인가.
4. 지금 CLI에서 무엇을 할 수 있는가.
5. 다음 작은 작업과 남은 한계는 무엇인가.

T0 #128은 완료했고 첫 코드 리팩터링은 T1.1 #129다. T0는 Issue와 phase를
전환했지만 서비스 중지, runtime 배포, 데이터 변경 또는 코드 리팩터링을 수행하지 않았다.
