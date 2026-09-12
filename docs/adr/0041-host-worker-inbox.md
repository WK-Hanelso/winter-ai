# ADR 0041 — Host 비차단 Worker와 Shared Core 결과 inbox

## 계약

일반 대화는 Docker의 기존 Shared Core/로컬 모델에서 처리한다. 명시적으로 승인한 읽기 작업만
별도 Host 프로세스가 실행한다. 이 프로세스에는 현재 승인한 요청과 공유 프로젝트 범위만
전달하며 개인 대화·Memory·Identity·Reference는 전달하지 않는다. 기존 typed gateway의
권한과 provider 인증 경계는 바꾸지 않는다.

기존 `SqliteOwnedWorkRepository` 구현을 전용 `data/worker_sessions/jobs.sqlite`에서 재사용한다.
기존 #126 저장소와 혼용하지 않으며 기본 resume 정책도 변경하지 않는다. 새 경로는 살아 있는
Worker를 보존하고, 중단된 Worker는 실패로 남긴다. 새 승인 없이 외부 실행을 재시도하지 않는다.
UI 종료는 승인된 Worker 취소가 아니다. 이미 시작된 작업은 끝까지 처리하고 결과를 보관한다.

## 전달 순서

Host 승인 → durable queue → 별도 Worker 실행 → durable result → Core 저장 → 화면 알림 → ack.

Host가 실제 터미널 fd를 poll하여 입력이 없어도 결과를 전달한다. Local Core의 한 답변을
생성하는 동안에는 notification을 끼워 넣지 않는다. stdin과 child protocol은 별개이며 모델
출력 문자열을 control message로 해석하지 않는다. 하나의 UI가 inbox를 소유하도록 lock을 둔다.

`ConversationRepository.append_once` 선택 Port는 job key에 대해 대화 원문을 한 번 저장한다.
SQLite는 기존 행을 보존하면서 부가 `conversation_deliveries` table을 필요할 때 추가한다.
저장된 메시지 시각을 Event 시각으로 사용해 재전달 시에도 사건의 identity가 변하지 않는다.
Event에는 원문 대신 job ID·상태·conversation 참조만 남긴다.

Core commit 후 UI crash에서는 저장 중복 없이 재전달한다. 화면 flush와 DB ack를 원자적으로
묶을 수 없으므로 화면 표시는 at-least-once다. Worker 완료/전달/사용자 수락을 구분한다.

## 검증 및 한계

계약 테스트: `tests/unit/test_background_text.py`. 원문 보존·backup 복원·동시 대화·입력 없는
알림·미전달 결과·중단 차단·중복 job 차단·idempotent conversation/event를 검사한다.
실제 평가 원문과 판정은 로컬 전용 인계에 보관한다. 공개 Issue/성숙도를 자동 승격하지 않는다.

동시 작업은 하나이며 요청 길이는800자로 제한한다. OS 재부팅이나 Worker 강제 종료 후
미완료 외부 작업의 실행 여부를 완벽히 알 수 없으므로 재실행하지 않는다. 기존 PID 생존 검사에는
PID 재사용의 한계가 있다. 일반 자동 위임·code write·capability 채택은 이 계약 밖이다.
