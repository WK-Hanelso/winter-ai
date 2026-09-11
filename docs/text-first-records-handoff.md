# Text-first 기록 인계표

- 결정일: 2026-09-10
- Epic: [#127](https://github.com/WK-Hanelso/winter-ai/issues/127)
- 전환: [T0 #128](https://github.com/WK-Hanelso/winter-ai/issues/128)
- 첫 코드 작업: [T1.1 #129](https://github.com/WK-Hanelso/winter-ai/issues/129), 다음 active scope/코드 미착수
- 결정: [ADR-0031](adr/0031-text-first-transition-and-history.md)

## 1. 이전 작업을 이어받는 방법

2026-09-11 전략 보완: [ADR 0039](adr/0039-dialogue-capability-first.md)는 사용자와 Gemini
제안을 검토한 대화의 채택/보류 결정을 기록한다. #132~#136 실패 → #137 원인 분리 →
T2.7~T2.9 모델/예시/CLI로 연결하며 과거 정책·Reference·DB를 대체하거나 초기화하지 않는다.

기존 기록은 과거라는 이유로 무시하지 않는다. 원본은 그대로 두고 현재 요약이 근거 위치와
후속 작업을 가리키게 한다. 새 결정이 예전 결정을 대체하면 날짜·사용자 결정·대체 범위를
함께 기록한다. 이미 실패한 접근을 다시 검토할 때는 바뀐 조건부터 설명한다.

전환 전 코드 기준은 `0c4f55775a3666a27a06e17361189a5e3a354169`이다.
`feat/126-winter-owned-work-canary` 브랜치를 보존하고 새 T0 브랜치는 그 커밋의 후손으로
만든다. 기존 이력을 squash/rebase/reset하지 않는다. 설계/TODO는 그 위의 새 결정으로 추가한다.
로컬 `main`과 tracking ref `origin/main`은 `31c7223`이며 전환 baseline은 main 이후
90개 커밋을 포함한다. tracking ref는 원격의 실시간 상태나 원격 백업 증명이 아니다.
T0 문서 커밋의 부모 체인을 보존하며 기존 90개를 main에 자동 병합하지 않는다.

## 2. 기록 종류별 보존 계약

| 기록 | 원본 위치/관리 | 새 구조로 이어가는 방법 | T0에서 수행한 범위 |
| --- | --- | --- | --- |
| 코드·테스트 | 기존 Git commit/branch | 새 branch의 조상으로 유지, Issue별 변경 | 기준 SHA 기록과 T0 branch 분리 |
| 개발 결정 | AGENTS, V1, ADR, Issue/PR | 최신 결정에서 대체한 항목과 원문을 연결 | ADR-0031과 이 표 추가 |
| 실험/평가 | `docs/`, ADR와 외장 private reports | 모델·조건·실패 축과 후속 gate를 재사용 | 문서 참조 연결; 원본 report는 열거나 변경하지 않음 |
| 겨울이 대화 | 기존 로컬 Conversation SQLite | stable evidence ID 유지, T3 선택적 조회/T4 갱신 | DB 열기·변환·삭제 없음 |
| Memory/Identity/Belief/약속 | 기존 private DB와 Identity 파일 | 기존 활성/후보/정정 상태를 유지하며 adapter 뒤로 이전 | 값·ID·경로 변경 없음 |
| Reference/모델/음성 | 기존 외장 저장소·로컬 모델 경로 | 결합된 원본·script·음성을 보존, Voice 재개 때 사용 | 이동·재수집·삭제·업로드 없음 |
| 작업 queue/inbox | 기존 SQLite/worker 기록 | T5 TaskService에서 기존 상태와 미전달 결과 재사용 | job 실행/소비/서비스 재시작 없음 |
| Codex/Claude 개발 세션 | 각 도구의 세션 원문 | 접근 가능한 명시적 결정만 ADR/Issue에 출처를 남겨 정리 | 현재 대화의 결정만 반영; 전체 세션 수집/겨울이 DB 주입 없음 |

Git 보존은 개인 DB backup이 아니다. T0는 데이터 파일의 새 backup을 생성하지 않았다.
향후 schema/index 변경 전에는 일관된 SQLite backup과 복사본 migration을 검증한다. 실행 중인
DB 파일만 단순 복사해 WAL 변경을 잃지 않도록 SQLite backup 기능 또는 명시적 정지 시점의
방법을 사용한다. schema version·row ID/evidence·원본/파생 관계 검증과 복원 방법을 해당 Issue에
기록한다. 개인정보가 담긴 backup은 Git에 넣지 않는다.

## 3. 원문 근거 → 후속 작업 연결

| 기존 근거 | 계속 사용하는 것 | 한계/실패를 이어받는 방법 | 새 작업 |
| --- | --- | --- | --- |
| [M2 검증](milestone-2-validation.md), [memory evidence](memory-evidence-temporal.md), #116 | SQLite, 정체성, evidence, 명시적/제한 직접 기억 | 자동 추론은 그대로 미활성, 기존 ID와 삭제 계약 보존 | T1/T3/T4 |
| [대화 말투](verbal-style-profiles.md), [말투 평가](style-holdout-evaluation.md), #92/#96 | 기존 profile과 비교 baseline | 분포 일치를 자연스러운 상황 반응의 성공으로 해석하지 않음 | T2 |
| [내용 평가](content-holdout-evaluation.md), #106 | 쌍 데이터와 원문 비교 도구 | 0.157은 원문 유사도 지표, 제품 대화 품질 점수 아님 | T2/T3 |
| [응답 근거 gate](claim-evidence-gate.md), #117 | 사실/신체/기억/capability 주장 검사 | lexical 검사의 범위와 정상 발화 과잉 차단도 평가 | T1/T2 |
| [이해](turn-understanding.md), [결과 평가](outcome-evaluator.md), [장부](v1-maturity-ledger.md) | Shadow queue/schema/실패 사례 | 이해 5/10, Outcome 18/30, 실제 review 부족 상태 유지 | T2/T4 |
| [maintenance](maintenance-worker.md), #118 | queue·defer fairness·재개 | 운영 관찰과 내용 품질 gate는 별개; 시간 경과만으로 24h 완료 처리 안 함 | T4 |
| [Day Reflection](day-reflection.md), [장부](v1-maturity-ledger.md) | 실제 사건 기반 회고·관점 이력 | 실제 3일 평가 미완료, 과거 사건을 허구로 backfill하지 않음 | T4 |
| [SelfModel](self-model-design.md), #119/#120 | CapabilityRegistry/ImprovementLedger | 전체 자기 코드 개선을 text 대화의 선행조건으로 두지 않음 | T1/T5 |
| [Work Orchestration](work-orchestration.md), #126 | background job/inbox, foreground scheduler, typed gateway | 아래 partial 결과와 이미 시작된 generation의 점유 한계 유지 | T5 |
| [adoption sandbox](adr/0028-sandboxed-adoption-verification.md), #121~#125 | 승인·검증·채택·rollback Shadow 코드 | 실제 write 자동 활성화 금지, 향후 write 작업에서 사용 | 후속 Level 3 |
| [Human Reference](human-reference-design.md), #66/#75 | 결합 corpus·정렬·화자·말투 자료 | 원본 보존, 새로운 원본 품질/학습 성공을 주장하지 않음 | T2의 참고/Voice 재개 |
| [Voice 결정](voice-path-decision.md), [실시간 평가](realtime-readiness.md) | 현재 adapter와 rollback·평가 원본 | 사람 수준 자연스러움 실패와 STT 오류 보존 | Voice 후순위 |
| [전체 감사](project-audit-2026-08-19.md), [baseline](baseline-consolidation-2026-08-19.md) | 원래 결함과 복구 이유 | 이후 해결된 것은 #114~#125와 최신 장부를 따라 확인 | 전 단계 |

## 4. #126 보류 상태

이 Issue는 성공으로 종료하지 않고 open 상태에서 partial/보류를 기록한다.
[GitHub 인계 기록](https://github.com/WK-Hanelso/winter-ai/issues/126#issuecomment-5611657530)

- 최초 동기식 task 98.77초: 실행 경로 완주, 근거 밖 추천으로 제품 gate 실패.
- 후속 background: 접수 text 0.000초, 직후 일반 대화 2.138초, 접수 TTS 2.450초,
  전체 82.83초. 비차단은 확인했지만 한 가지 추천을 확정하지 못해 partial.
- SQLite recovery와 queued foreground 우선권 근거는 T5로 계승한다.
- actual phone 동시 사용, generation preemption, lease heartbeat는 미검증으로 남긴다.
- phone canary 확대와 새 모델 반복 탐색은 현재 active 작업이 아니다.

T0는 위 수치를 재측정하지 않았다. 2026-08-21의 배포 기록과 오늘 서비스가 살아 있는지는
다른 사실이다. 이번에 runtime을 재시작·중지하거나 자동 task를 실행하지 않았다.

## 5. 앞으로 이 표를 갱신하는 규칙

모듈을 교체한 Issue는 이전 코드/ADR, 새 위치, migration 여부, 재검증 결과와 남은 gate를
한 행으로 연결한다. 시험에 실패한 후보는 조건과 실패를 남기고 기존 안정 경로를 보존한다.
원본을 직접 읽어야 하는 것은 조사 대상이 된 부분이며, 모든 문서를 매번 전체 재독하는
방식으로 인계를 유지하지 않는다. 사용자 삭제 요청은 데이터 보존 정책보다 우선한다.
