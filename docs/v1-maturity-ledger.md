# Winter V1 Maturity and Enhancement Ledger

- 최초 기록: 2026-08-15
- 목적: 구현된 기능을 완성형으로 오해하지 않고 현재 성숙도, 실제 근거, 실패와 다음
  고도화 gate를 지속적으로 유지한다.

기계 판독 가능한 현재 상태와 선행조건은 [`../project-state.toml`](../project-state.toml)을
기준으로 한다. 아래 자유 서술과 상태 파일이 충돌하면 `./winter project-check`가 실패하며,
새 기능 확장 전에 상태와 근거를 먼저 바로잡는다.

<!-- winter-project-state: capability_registry=operational, development_control_gate=operational, improvement_ledger=operational, self_model=operational, text_first_foundation=contract, work_orchestration=shadow -->

## 2026-09-10 우선순위 전환

Text-first Epic #127 / T0 #128의 기준 전환과 기록 인계를 완료했다. 새 `text_first_foundation`은 **contract**이며
기존 20개 기능 maturity는 그대로다. T1.1 #129 bootstrap 추출을 완료했고 다음 active T1.2 #130은
코드 미착수다. Voice 고도화와 #126 actual-phone
관찰은 보류하고 #126의 결과는 partial로 남긴다. operational 표시는 기존 검증 범위를 뜻하며
이번 문서 전환으로 서비스의 오늘 상태를 재확인했다는 뜻이 아니다.

아래 실패·관찰 대기 기록을 지우지 않는다. [기록 인계표](text-first-records-handoff.md)가
이전 근거와 T1~T6의 재사용/재검증 지점을 연결한다. 특히 Shadow Memory/Belief 활성화와
write Worker는 기존 품질·승인 gate를 유지한다.

### T1.1 #129 — 초기화 경계 추출 완료

- 가능 범위: Python config로 기존 text Core와 저장소를 조립한다. CLI 옵션/관리 명령,
  prompt 정책, 모델과 DB schema는 유지하고 음성 runtime 없이 시작한다.
- 근거: `src/companion/application/bootstrap.py`, `tests/unit/test_text_bootstrap.py`,
  [ADR 0032](adr/0032-text-runtime-bootstrap.md). Docker offline 847 passed (39.96초),
  Ruff 전체 통과, mypy 176 source files 통과.
- 한계: 운영 서비스/개인 DB/모델을 이번 작업에서 재검증하지 않았다. close는 pending job을
  취소하지 않는 bounded drain이다. Web/Voice factory 이전과 Worker 종료 개선은 별도다.
- gate: 자연스러운 말투·기억의 연속성·Agent 활용이라는 Text-first 전체 목표는 아직 contract다.
  이번 리팩터링만으로 운영 품질이나 기존 실패 gate를 승격하지 않는다.
- 다음: #130 정체성 snapshot과 문맥 조립 추출, 이후 T2 실제 텍스트 정책 비교.

## 개발 통제 상태

Development Control Gate V0는 현재 phase, active Issue, 제한된 maturity vocabulary,
operational evidence와 activation dependency를 offline에서 검사한다. 상태 marker를 가진
README·Roadmap·Ledger의 명시적 판정도 함께 비교한다. 아직 free-form 문장의 의미 충돌이나
dirty worktree의 파일별 Issue scope를 자동 판정하지는 못하므로, baseline consolidation 뒤
diff scope 검사를 다음 고도화로 남긴다.

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
| Conversational Maintenance | 3, Shadow 운영 관찰 중 | Shared Core 자동 queue와 응답 분리. 전용 CPU Qwen3-4B `:18081`·user systemd 2개 배포. copied DB 5/5와 live 1/1 canary, 4-thread 3건 평균 42.07초, synthetic 대화 요청 idle 0.300초/부하 0.285초. aggregate log, batch 1·5초 휴식과 live cycle-boundary restart 통과. 동일 timeout row 8회+ 연속 defer starvation을 발견해 persistent defer audit와 least-deferred rotation 구현, 집중 37·전체 749 tests와 copied DB 불변 migration 통과. 운영에서 timeout row 120.1초 defer count 1 뒤 다음 row가 94.9초 completed, aggregate 17/8/177·maintenance restart 0·Memory 10 | 실제 다양성에서 exact evidence 불일치 6·confidence 형식 오류 2로 최근 처리 24건 중 8건 차단. 24시간 supervisor·실제 대화 latency·사람 품질 audit 미완료. TurnUnderstanding 자체 5/10 gate 실패이며 결과 소비 미연결 | 서비스 생존과 결과 activation을 분리 판정하고 24시간 queue/restart/latency·사람 audit 전 소비자 연결 금지 |
| Direct Memory / Reflection / Current State | 3, 검토형 | 상태 후보→검증→사용자 승인→Timeline 수직 단면, development 14개와 locked-v1 12개 평가기. evidence 시스템 고정·state/장기 중복 차단 뒤 CPU Qwen은 8/14와 6/12, 두 세트 topic P 50.0%·R 28.6%, unsafe update 2로 gate 실패. 이후 무맥락 대명사 fail-closed와 후보별 형식 오류 격리·SQLite/overview warning 추가; 실제 상태 불변 | 지속 상태·context 대명사 누락, multi-topic merge, 새 topic·alias 없음, scene당 약 15~52초. 안전 변경 뒤 locked 재측정은 의도적으로 보류 | 지속·절 분해·grounding 판단 구조 개선 후 다음 version에서 두 전체 suite를 재평가해 warning 0, topic/evidence P 100%·R 80%·negative FP 0 |
| Direct Memory provenance | 4, 제한 운영 | Conversation append receipt, exact `conversation:<id>` evidence, durable/episodic/unknown temporal scope와 additive legacy migration. broad identity-shaped fixture fail-closed. 집중 59·전체 702 tests, 원본 DB 10/10 row 보존, Web restart·HTTPS health와 구버전 schema rollback backup 확인 | generic 직업/정체성 진술도 고정 field가 아니면 자동 저장하지 않아 recall보다 precision을 우선함. 새 자연 발화 provenance audit와 사용자 체감 전 | 자연 대화에서 stable preference recall·state 비활성화를 관찰하고 provenance ref 존재를 내용 없이 audit |
| TurnUnderstanding | 3, Shadow | schema, exact evidence, JSON Schema, 10 scene 평가 | 최종 5/10; preference/project temporal·proposal 누락 | 문서화된 activation 기준 전부 통과 |
| OutcomeEvaluator | 3, Shadow | provenance와 blind review DB v3; 새 locked v2 18/30, synthetic 7/7 gates, 실제 검토 CLI·audit 동작 | corrected/rejected·continuation·ambiguous 세부 필드 실패, 실제 blind review 0/30 | 천우의 실제 Shadow 30+ blind audit 후 candidate-only activation |
| DialogueDirector / review | 3 | 행동 선택, 길이 제한, 일부 회피 응답 repair 테스트 | 선택 품질이 TurnUnderstanding/Outcome으로 학습되지 않음 | 실제 대화 outcome 기반 행동별 성공률 |
| Grounded Self-Presence | 2, 제한 운영 | Winter 직접 초대 감지→`self_reciprocate`와 lexical 칭찬·동의·질문·일부 활동 repair. development scene 1.29초와 continuity 14/14 | locked-v1 lexical 자동 40/41이나 사람 판정 4/12로 일반 gate 실패. unsupported 현재 상태·capability 회피·동조·이름 혼동. active Belief 0개, 자기 이력 없음 | failure class 기반 SelfState/SelfModel 뒤 새 held-out 12+ scene에서 grounding·일관성·비동조·capability honesty |
| Response claim authorization | 4, 제한 운영 | final Core gate가 body/outside/user-cause/memory/capability claim을 current turn evidence와 대조하고 한 repair 뒤 honest fallback. locked-v2 unsafe 6/6 차단·valid 8/8 허용, 실제 Core 2회 사람 검토, 전체 722 tests 뒤 Web 새 PID·local/Tailscale health 확인 | 한국어 pattern의 측정 범위이며 모든 사실성 보장 아님. 첫 실제 run의 unsupported distress cause는 v2에서 차단했지만 한 stochastic opinion overblock 관찰. 이름 혼동·동조·대화 회피는 별도 품질 실패 | 일상 대화에서 false accept·overblock 관찰, 새 실패는 v3로만 추가 |
| Lived Continuity / Winter Event | 1, turn 기록 운영 | Shared Core의 완료 turn을 한국 시간 append-only Event로 남기고 날짜별 사건·출처를 원문 없이 `./winter lived`로 점검. Shadow Outcome을 prior turn subject에 연결하고 3 turn/2 days/supportive Outcome→Belief activation 통합 테스트 통과 | 기존 대화 backfill 없음. Outcome 분석은 gate 실패·background 미배포, 결정·capability event 미연결, 장시간 동시성 미검증 | 실제 CLI·Web·Voice event를 7일 관찰하고 누락·중복·privacy·날짜 경계 audit |
| Day Reflection | 1, 자동 실행 준비 | 실제 양방향 대화+Winter Event가 있는 완료 날짜만 최대 5개 근거 문장으로 한 번 닫음. 04:10 oneshot, 실패 재시도, fake 차단, immutable date, 같은 Core에서 최근 2일 회고 공유를 offline 검증 | 운영 Event day가 아직 0이라 실제 local-model 회고와 다음 대화 효과는 미검증. evidence id는 provenance이지 의미 정확성 보장이 아님 | 실제 3일에서 허구 사건·사용자 상태 단정·stale callback 0, 다음 대화 자연스러움 사람 평가 |
| Open Loop | 3 | 명시적 미완료 주제 저장·재시작 callback | 간접 약속·완료 판정·expiry 부족 | Outcome으로 continued/abandoned 검증 |
| Winter Belief | 3 manual / 1 autonomous | 근거·confidence·revision lifecycle, active만 prompt 사용. 저위험 namespace에 confidence 0.80·3 exact events·2 days·지지 Outcome gate. Belief 상태+audit 단일 transaction과 Event projection retry. 실제 Outcome/Event의 지지·교정 근거를 subject별 ephemeral window로 묶고 stable id로 한 번만 분석하는 Shadow batch와 20개 semantic-safety locked-v1 채점기 구현 | Local proposal는 Shadow DB에만 저장. locked 정답표·채점기는 offline 검증했지만 실제 별도 analysis model 점수와 사람 audit 0건. proposal 생성기 자체 held-out, background 실행·operative rollback 없음 | 실제 모델 20 scene에서 user-fact·Identity·capability·dependency FP 0, real Shadow 20+ 사람 audit, proposal-selection gate 뒤 candidate 연결 |
| Verbal Style | 3 | Reference 기반 profile과 held-out 문장 길이 평가 | Zeta/Polybuzz 수준의 관계 행동·장기 변화 아님 | 장시간 joint dialogue 선호 평가 |
| Voice identity | 2, 제한 운영 | 이전 blind에서 현재 Seed-VC보다 높았던 Chatterbox S3Gen Reference를 직접 출력. 고정 문장 warm 2.21~2.27초, old two-stage 4.82초. Seed-VC rollback 보존 | 모든 기존 후보가 자연스러움·질문 억양·사람 착각 수준 미달. 직접 생성 3회 중 1회 구절 반복 | 실제 일상 청취와 새 voice path blind gate, 질문·고유명사 content fidelity |
| Phone STT/Web | 2, 제한 운영 | push-to-talk→SenseVoice Q8→Core→직접 Chatterbox SSE audio 동작. 기존 Whisper+2단계 실제 폰 first-play p50 9.68초. 새 직접 경로 실제 폰 4턴 first-play p50 4.41초·p95 5.03초, release-to-STT p50 0.90초. path별 privacy-minimal latency DB 분리 | 4/20으로 표본 부족, 수동 transcript 2/4 materially wrong, 성공 turn만 기록해 failure rate 없음. VAD·barge-in·network 복구와 사람 수준 음질 미달 | 실제 폰 20 turn과 STT held-out 뒤 first-audio p50≤3초·p95≤5초 |
| Web ingress safety | 4, 제한 운영 | 실제 user service가 loopback에만 listen하고 Tailscale Serve HTTPS가 이를 전달함. live health와 16 MiB 초과 `/turn`의 HTTP 413, offline 400/411/413 회귀 확인 | tailnet 계정 접근통제에 의존하며 앱별 사용자 인증·rate limit·slow-client timeout은 없음 | 실사용 failure 지표 뒤 timeout/rate-limit 필요성을 별도 판단 |
| Web turn safety | 4, 제한 운영 | full-turn lock, Core thread 예외 회수, SSE 단일 writer. failure·concurrency deterministic regression과 전체 691 tests 통과 뒤 실제 user service 재시작, loopback·Tailscale HTTPS health 확인 | FIFO 대기시간 표시·client cancellation·자동 retry 없음. 실제 terminal error 사람 관찰 전 | 일상 실사용에서 오류 표시·대기 경험을 관찰하고 불편이 측정될 때만 queue status/cancellation 고도화 |
| SelfModel / Capability Registry | 3, 제한 운영 | 별도 SQLite에 definition·version·limitation·machine claim scope·dependency·evidence·전이 history를 보존. 최신 health+test/evaluation+dependency gate와 runtime 연결 상태로 turn별 read-only snapshot을 만들고 CLI·Web·Voice가 공유. conversation·memory만 foundation capability로 등록하고 final claim gate가 미등록 project write·web·account·system 주장을 차단. #124 synthetic cycle은 exact adopted change와 rollback target으로 temporary capability의 available·retired 전이를 source apply/rollback과 연결. #125는 채택 전체 검증을 read-only/no-network/no-HOME sandbox로 격리했다. 전체 792 tests, Ruff와 mypy 171 files 통과 | available은 temporary Registry Shadow일 뿐 운영 runtime에 연결되지 않음. capability 질문의 실제 자유 대화 표본과 장시간 cross-interface 검증 전. runtime health 실패의 자동 degraded 전이와 실제 실행 권한 미연결 | 실제 작은 capability를 사람 검토로 연결하고 runtime health/degraded/rollback 검증 |
| Improvement Ledger / proposal | 2, 기반 운영 + lifecycle Shadow | 별도 SQLite에 stable evidence의 observed→discussing→immutable proposed version/hash→explicit implementation approved를 보존. #124는 `ready_to_adopt→adoption_approved→adopted`로 승인과 실제 완료를 분리한다. approval은 Improvement/Worker/validation/ChangeProposal/patch/baseline/capability spec SHA-256을 묶고, source applied와 Capability available 뒤에만 completion을 기록한다. #125 sandbox verifier 실패·소스 변조는 before bytes 복원 뒤 adopted를 차단한다. CLI·Web·Voice 공통 read-only snapshot과 aggregate audit 유지 | 사용자-facing 자동 adoption route는 미연결. 여러 SQLite를 단일 transaction으로 묶지 않고 persistent state reconcile을 사용. evidence reference source row 교차 검증 전. 한국어 진행 표현 gate는 범용 의미 판별 아님 | 실제 작은 capability의 사람 승인·재평가를 연결 |
| Worker / Level 3 | 2, read-only 제한 운영 + write Shadow | #121~#123의 lifecycle, isolated proposal와 source recovery를 #124 exact coordinator가 결합. #125는 isolated Worker와 source-adoption verifier가 공용 bubblewrap을 사용하게 하고, 정상 exact cycle과 실제 hostile probe에서 read-only source, private `/tmp`, no-network/no-HOME/credential, fixed argv, timeout/no-host-fallback을 검증했다. source/host write·symlink secret·loopback/external network 공격과 timeout 뒤 background child는 차단됐다. 전체 792 tests, Ruff, mypy 171 files 통과 | 실제 Codex write Worker, 운영 source, user route, runtime capability는 미연결. cross-DB atomic transaction·hostile concurrent writer lock·CPU/memory limit 없음. Worker 실행 뒤 patch 저장 전 crash는 수동 복구 | 천우가 고른 실제 작은 capability를 별도 승인과 사람 검토 아래 전체 cycle로 완주 |
| Winter Work Orchestration | 3, Shadow + actual-phone canary | #126은 동기식 98.77초 turn을 single-job background coordinator와 Web completion inbox로 분리했다. 실제 동일 task에서 접수 text 0.000초, 직후 일반 Local 대화 2.138초, 접수 문장 Chatterbox 2.450초, 전체 82.83초. Local review는 report claim 중 핵심·추천만 선택하고 evidence ID를 코드가 계산해 새 사실 추가를 차단. SQLite job/inbox는 actual child-process crash 뒤 running request와 미전달 결과를 복구한다. queued foreground-first scheduler는 작업 접수·대화 종료·폰 녹음 시작부터 6초 quiet window를 보장하며 실제 `:18080` 겹침 probe에서 foreground 0.498초, background 6.855초였다. 2026-08-21 제한 drop-in 배포와 loopback/Tailscale HTTPS health 확인. source 불변, offline 820 tests·Ruff·mypy 107 files 통과 | 실제 phone 동시 대화 표본은 아직 0건이고 결과는 현재 자료로 다음 한 가지를 확정하지 못해 partial. 이미 시작한 Local generation preemption·lease heartbeat 미검증. SQLite 결과와 Conversation append는 단일 transaction이 아님. 선택해 읽은 project text는 외부 Codex 처리 경로 사용 | phone에서 작업 접수→즉시 후속 대화→완료 복귀 측정·사람 판정 뒤 동일 task 유용성 gate를 거쳐 20 task로 이동 |

현재 검증 기준선은 2026-08-20의 오프라인 test 820개, Ruff 전체와
`mypy src` 107개 source file 통과다. 실제 model probe와 Voice latency는
offline 회귀와 분리해 각
기능 행의 근거로 기록한다.

Issue #114의 Web ingress safety는 loopback 기본값·명시적 systemd host·16 MiB body limit과
HTTP 400/411/413 offline 회귀를 구현했다. 설치된 user service, 실제 loopback socket,
live HTTP 413, Tailscale Serve HTTPS health까지 확인해 제한 운영으로 승격했다. 변경하지 않은
폰 음성 기능의 재시연은 이 보안 수정의 gate가 아니며 이후 일상 대화에서 관찰한다.

## 지속 갱신 규칙

기능을 추가하거나 수정할 때 다음을 같은 작업 안에서 갱신한다.

1. 현재 가능한 범위
2. 검증 명령과 실제 수치
3. 아직 실패하거나 측정하지 않은 범위
4. 운영 상태에 연결할 activation gate
5. 다음 고도화 작업

## 사용자 관찰 대기

자동 테스트·서버 검증과 천우가 실제로 느껴 본 결과를 구분한다. 아래 항목은 개발이나 정상
사용을 막지 않으며, 천우가 평소 사용 중 확인했을 때만 관찰 완료로 바꾼다. Codex는 상태를
보고할 때 미확인 항목을 계속 알린다.

- #129 text bootstrap 이후 실제 Local LLM CLI 대화: offline 회귀는 완료했지만 이번 변경의
  사용자 체감은 미확인이다. 사용 명령은 유지하며 비차단 관찰로 남긴다.

- #114 이후 폰 push-to-talk 한 turn: ingress만 바뀌었고 voice 기능은 변경하지 않아 비차단.
- #115 실제 오류의 화면 표시와 빠른 복수 요청의 대기감: deterministic 회귀와 운영 배포는
  완료했지만 천우의 실제 관찰은 아직 없음.
- #116 자연 대화의 안정 preference recall과 순간 상태·자기평가 비활성화: migration과 운영
  배포는 완료했지만 synthetic turn으로 실제 대화 DB를 오염시키지 않아 천우의 관찰은 아직
  없음.
- #117 실제 대화의 근거 없는 신체 행동·사용자 원인 차단과 정상 의견 과잉 차단 여부:
  locked-v2·실제 Local Core 임시 평가·운영 배포는 완료했지만 천우의 일상 관찰은 아직 없음.
- #118 background maintenance의 대화 latency 영향·재시작 안정성과 다양한 실제 결과 품질:
  초기 canary와 graceful restart는 통과했지만 24시간 관찰과 사람 audit은 아직 진행 중.
- #119 겨울이에게 실제 능력과 한계를 자연스럽게 물었을 때의 정확성·과잉 차단 여부:
  offline contract와 운영 Core probe 뒤에도 천우의 일상 대화 관찰은 별도로 남긴다.
- #120 실제 개선 안건이 생긴 뒤 상태 설명이 자연스러운지와 정상적인 `해결 중` 표현까지
  과잉 차단하는지: synthetic approved probe는 통과했지만 운영 Ledger는 합의 없이 비워 뒀다.

새 기능은 기본 Level 1 또는 2에서 시작한다. 문서나 단일 성공 사례만으로 Level을 올리지
않으며, 실패 결과도 삭제하지 않고 다음 판단의 baseline으로 남긴다.
