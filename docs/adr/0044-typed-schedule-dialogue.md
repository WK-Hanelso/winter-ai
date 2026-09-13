# ADR 0044 — Typed 일정 대화와 실행 결과 표시

상태: Text-first P2에 적용(2026-09-13). 실모델 6장면 v4 검증 완료,
7일 실사용 관찰 중. 사용자 관찰 대기이며 대화 품질 수락·성숙도 승격은 아니다.

## 결정과 이유

모델이 일정 의도와 자연스러운 답변을 제안하고 데이터 계층이 날짜·대상·쓰기 결과를
확정한다. 모델의 저장 완료 발언을 실제 DB 반영의 근거로 삼지 않는다.

- 기본 한 번의 모델 호출로 `reply_draft`와 `schedule_proposal` JSON을 받는다.
  llama.cpp 서버의 `response_format.json_schema.schema`로 구조 문법을 제약하고
  Python이 파싱·의미 검증한다. 구조 문법 성공은 의도 이해 성공을 뜻하지 않는다.
- 모델은 create/update/cancel/query와 원문 필드만 제안하며 SQL·revision·command ID를
  만들지 않는다. Core/`ScheduleService`가 현재 원문, KST 날짜, 대상과 revision을 확인하고
  `knowledge.sqlite`의 일정·변경 이력을 transaction으로 함께 기록한다. 후보 둘 이상은 되묻는다.
- 프롬프트는 Identity·예시·고정 지침과 일정 계약을 먼저 두고 가변 상태·기억·일정,
  최근 대화를 잇는다. 확인된 데이터와 실행 receipt를 초안의 사실 근거로 사용한다.
- commit 확인 뒤 receipt와 충돌하지 않는 초안을 표시한다. 모든 필드의 나열은 요구하지
  않는다. 조회는 확인된 문맥과 대조하고, 되묻기·충돌은 receipt로 답변을 다시 생성한다.
  현재 실행 receipt 없는 새 완료 주장은 표시하지 않는다. 실패·예산 소진은 receipt 문구로 반환한다.
- JSON/불완전 출력 재시도와 receipt 재생성을 합쳐 턴당 호출은 최대 2회다.
  두 번째 답변은 일정 제안을 실행하지 않으며 저장을 반복하지 않는다.
- memory/body/outside claim은 차단·repair 대신 `claim_observations.jsonl`에 관찰 기록을
  남긴다. capability/improvement_state 차단과 일정 receipt 표시는 유지한다.
- P2.6 날짜: 단독 요일은 가장 가까운 미래 요일이며 당일이면 확인한다. `이번 주 X요일`은
  월요일 시작 기준으로 계산하되 지난 요일은 다음 해당 요일로 보정한다. 과거 명시 날짜는
  확인하며 계산된 날짜·요일을 표시한다. 이번 주 전체·주말 조회 범위는 기존대로 유지한다.

## 대안과 한계

일정 의도 판별과 답변 생성을 매번 별도 호출하는 대안은 지연을 늘리고, 모델에게 SQL이나
날짜 확정을 맡기면 실행 경계가 흐려진다. typed 제안과 receipt 분리를 유지하는 방식을 권장한다.
현재 원문과 제한된 한국어 날짜·대상·초안 검사에는 해석 한계가 있다. 대기 제안은 Core 내
일시 상태이며 재시작 복원 대상이 아니다. 외부 예약·캘린더 연동은 없고 관찰형 claim은
허위 기억·신체·화면 밖 활동 발언을 막아 주지 않는다. 장기 자연스러움은 실사용 관찰 대상이다.

검증: `test_test_storage_guard.py`, `test_claim_observations.py`, `test_schedules.py`,
`test_schedule_dialogue.py`, `test_schedule_p23.py`, `test_schedule_p25.py`, `test_schedule_p26.py`.
개인 DB·실모델 평가 원문은 공개 근거에 포함하지 않는다.
