# Text-first Winter 구현 TODO

2026-09-13 현재 단계: P2 실모델 6장면 v4 검증 완료, ctx 8192·실제 `data/`로 7일 실사용 관찰 중(2026-09-13~).
다음은 P3 캐릭터 대화다. **사용자 관찰 대기 · 품질 수락 아님**이며 기능 성숙도는 올리지 않는다.
아래 T번호·Issue·체크박스는 과거 공개 기록으로 보존한다. 이번 동기화에서 새 Issue 번호는 부여하지 않는다.

현재 초안 배포 인계는 로컬 `data/research/draft-delivery.md`를 먼저 확인한다.
같은 대화창의 승인형 Worker 연결과 명시적 기억 저장 후속은
`data/research/session-worker-delivery.md`에 이어 기록한다. 공개 Issue 상태를 임의로 변경하지 않는다.
이후 Host 비차단 작업·미전달 알림 복구는 `data/research/background-text-delivery.md`에 기록한다.

최신 로컬 실행형은 `./winter text`다. 코드 변경, 판단, 실제 평가와 다음 우선순위의 상세
인계는 Git에서 제외한 `data/research/text-foundation-delivery.md`에 있다. 아래 체크박스와
Issue 상태는 마지막 공개 동기화 기록이며 로컬 후속 작업을 미실행으로 단정하지 않는다.

- 작성일: 2026-09-10
- 상태: #128~#132 및 #134~#136 범위 완료, #137 로컬 진단 완료. T2 후보 품질 gate 실패·legacy 유지.
  다음 T2.7 모델/예시 조사는 Issue 미등록. GitHub #137 OPEN/동기화 보류로 active 유지, #133 보류.
- 설계: [Text-first 모듈 설계](text-first-refactoring-design.md).
- baseline: `0c4f557`, `feat/126-winter-owned-work-canary`.
- T번호는 계획상의 ID다. Epic #127, T0 #128, T1.1 #129, T1.2 #130, T2.1 #131, T2.2 #132, T3.1 #133을 등록했다.
- 기록 보존과 재사용: [기록 인계표](text-first-records-handoff.md).
- 사용자 승인으로 T3 전에 T2.3 응답 절단 → 검사 오탐/repair → 정책/모델 재평가를 우선한다.

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

2026-09-11 사용자 승인으로 T2 후반을 대화 능력 중심으로 보완했다.
[ADR 0039](adr/0039-dialogue-capability-first.md)가 결정/표현 경계/보류 범위를 정의한다.
과거 T2 정책 비교와 실패 원본을 보존하며 모델/예시 확보를 후속 핵심 작업으로 올린다.

```text
T0 작업 기준 전환
 → T1 기존 동작을 보존한 모듈 추출
 → T2 정책 비교·원인 분리 → 모델/예시 조사·비교 → 작은 CLI 대화 경험
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

T2.1~T2.6은 동일 모델 진단 기록이다. T2.7~T2.9는 승인된 전략 확장으로, 모델의 관계형
대화 능력과 문맥·예시를 함께 평가한다. 천우의 실제 선호와 자동 검사를 구분한다.

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

### T2.3 — Core 응답 보존 (#134)

- [x] 문장 수 기반 초안/repair/final 절단과 stream 조기 종료를 제거한다.
- [x] 기존 생성 지침/토큰 예산과 claim 규칙은 유지하고 전체 답변을 검사한다.
- [x] stream 및 미지원 대체 경로 모두 최종 검사 후 callback으로 전달한다.
- [x] #132 원문 replay로 전후 비교하고 Docker 회귀/문서/ADR을 기록한다.

**#134 완료:** 실제 원문 2건을 일반/stream/미지원 stream에서 재생해 내용·서식을 보존했다.
신규 21개 포함 Docker offline 919 passed (65.34초), Ruff/mypy(181 files) 통과.
테스트 서식만 정리 후 21개·Ruff·mypy 재통과. 신규 서버 호출/개인 DB 변경은 없다.
[전후 결과](evaluations/134-response-preservation.md), [ADR 0036](adr/0036-preserve-reviewed-response.md).

이번 범위는 내용을 잘라내는 후처리 오류다. 기존 Director의 복합 의도 오분류와 모델 자체의
부실한 설명, claim 오탐/누락은 별도다. replay는 새 실제 모델 품질 평가를 대신하지 않는다.
후속 순서: #135 검사 오탐/repair → 정책/모델 재평가 → T3 재개 판단. #133은 삭제하지 않는다.

### T2.4 — claim 검사와 repair 의미 보존 평가 (#135, 범위 완료)

- [x] #132/#134의 실패 최소 사례와 정상/위반 대조군을 먼저 고정한다.
- [x] 질문·부정·인용·자기 한계와 실제 주장을 구분하는 책임을 설계한다.
- [x] 전체 fallback 재검사의 오탐과 repair의 질문 주체/목적 변경을 검증한다.
- [x] 기존 기억/능력/활동 차단을 유지하고 같은 입력의 전후 결과를 보고한다.

**#135 범위 완료:** 신규 38개 포함 Docker offline 957 passed (49.00초), Ruff/mypy(182 files)
통과. 기존 과거 출력으로 검사 변화를 분리했다. 정상 질문의 불필요한 repair는 줄었지만
주체가 바뀐 답변은 사실 검사만으로 여전히 통과할 수 있다. 새 repair 지침의 의미 보존
성공은 미검증이다. [전후/실패](evaluations/135-claim-review.md),
[ADR 0037](adr/0037-claim-language-and-repair-boundary.md)를 따른다.

규칙을 제거하거나 장면별 예외를 쌓지 않는다. 모델 교체/개인 DB/Voice는 범위 밖이다.

### T2.5 — 수정된 Core 실제 텍스트 재평가 (#136, 실행 완료 / 품질 실패)

- [x] 현재 Local LLM 연결과 모델/config를 읽기 전용 확인한다.
- [x] 작은 합성 suite로 원 초안/검사/repair/최종 출력과 latency를 구분한다.
- [x] 질문 대상·목적·내용 유용성, 기억 모름/근거 있는 회상을 직접 검토한다.
- [x] 새 모델/정책 검토가 필요한지 또는 다른 병목인지 판단하고 다음 작은 작업을 정한다.

**#136 실행 완료 / 품질 실패:** 실제 28턴/31회 모델 호출, 저장한 선호는 회상했으나 모르는
취향은 양쪽 실패. 검사 오탐과 repair 주체 변경도 남았다. 원문/gate/최종을 따로 보존했다.
Docker offline 959 passed (53.94초), Ruff/mypy(182 files) 통과. 임시 평가 연결은 종료했다.
[결과](evaluations/136-text-reevaluation/README.md), [ADR 0038](adr/0038-postfix-text-evaluation-verdict.md).
어느 한 원인만으로 단정하지 않고 #137에서 최소 문맥 대조 뒤 다음 변경을 결정한다.

사용자 관찰 대기와 코드/모델 검증을 구분한다. 기존 실패를 새 held-out이라고 하지 않는다.

### T2.6 — 모델 최소 능력과 Core 문맥 간섭 분리 (#137, 로컬 실행 완료)

- [x] 같은 현재 입력/선택 기억/최근 대화에서 최소 지침과 전체 문맥의 비교 계약을 고정한다.
- [x] 같은 모델/config로 제한된 반복 표본을 실행하고 원 출력/latency를 기록한다.
- [x] 최소 조건부터 실패하는지, 전체 조건에서만 악화되는지 내용으로 검토한다.
- [x] 후속 방향을 모델/예시 조사로 정리하고 판정/한계를 사용자 보고에 포함한다.

2026-09-11 합성 7장면 × 2조건 × 3 seed = 42회, warm-up 2회. 최소에서도 기억/의견 실패,
전체 지침의 일부 유익과 검사 오탐을 함께 확인했다. [요약](evaluations/137-context-ablation/README.md),
[ADR 0040](adr/0040-context-ablation-verdict.md). Docker offline 969 passed, Ruff/mypy 통과.
원 응답/검토는 로컬 Git 제외로 보관한다. GitHub OPEN/동기화 보류이며 close/push하지 않았다.

원 출력은 진단 자료일 뿐 운영 gate 우회가 아니다. 모델 다운로드/교체·개인 DB·서비스·Voice는 제외한다.

### T2.7 — 관계형 대화 모델과 예시 자료 조사 (후속 Issue 등록 예정)

- [ ] 한국어 다중 턴 대화 근거, 공개 source/weight, 라이선스, Orin runtime/메모리 조건을 조사한다.
- [ ] 현재 A.X 기준선과 비교할 최대 2~3개 후보 또는 부적합 사유를 정리한다.
- [ ] 농담→정정, 분위기 변화, 자기 관점, 모르는/있는 기억, 몸 없는 표현을 판단 축으로 삼는다.
- [ ] 예시 자료의 문맥·반응·관찰 delivery/추정 감정을 구분하고 출처와 비공개 경계를 기록한다.

조사만으로 다운로드·모델 채택·학습을 하지 않는다. 기존 실패한 모델은 바뀐 조건이 있어야 재검토한다.

### T2.8 — 소수 모델/예시 격리 비교 (후속, 미착수)

- [ ] 후보별 template/runtime/양자화와 동일 입력·기억·예산 비교 계약을 고정한다.
- [ ] 모델 비교와 no-example/few-shot 비교를 분리하고 기존 장면/새 평가를 구분한다.
- [ ] 원 응답의 대화 적합성·사실 경계·latency와 후처리 간섭을 따로 기록한다.
- [ ] 개선 또는 실패 근거와 다음 선택을 보고한다. 순위만으로 운영에 채택하지 않는다.

### T2.9 — 기존 CLI의 작은 관계형 대화 경험 (후속, 미착수)

- [ ] 승인된 후보를 기존 Port/config에 연결하고 rollback과 기존 기억 호환성을 확인한다.
- [ ] 텍스트의 자연스러운 반응을 먼저 확인하며 행동 지문/한숨 token은 필수화하지 않는다.
- [ ] 천우의 실제 사용 관찰과 자동 검사 결과를 분리해 보고한다.
- [ ] 실패가 반복되면 정규식 확대 대신 데이터/모델/문맥 중 다음 scope를 합의한다.

SFT/선호 학습은 반복 실패와 데이터·학습 장비 근거가 있을 때 별도 Issue로 결정한다.
Voice 구현이나 학습 완료를 T3/T4/T5의 필수 선행조건으로 삼지 않는다.

## T3 — 모든 기록을 읽지 않고 기억 찾아오기

T2의 짧은 대화 기준선 뒤 이어가며 기존 기억은 전 단계에서 유지한다. 대화 모델의 완벽함을
기다리며 독립적으로 유용한 검색/정정 작업을 무기한 미루지 않는다.

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
