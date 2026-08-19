# Shadow Maintenance Worker

## 사용자 계약

천우가 하는 일은 겨울이와 대화하는 것뿐이다. CLI, Web, Voice의 Shared Core가
사용자 발화를 내부 queue에 자동 등록하고, 이 Worker가 응답 경로와 분리된 프로세스에서
그 queue를 처리한다. `reflect`, `memories`, `overview` 같은 명령은 개발·audit 도구이지
정상 사용 절차가 아니다.

현재 Worker의 범위는 **TurnUnderstanding Shadow event만 소비하는 것**이다. 분석 결과는
event log에 남지만 답변, active Memory, current state, DialogueDirector를 바꾸지 않는다.
Reflection/current-state 후보 생성과 Outcome 분석은 아직 이 상시 Worker에 연결하지 않았다.

```text
천우의 일반 대화
  → Shared Core가 원문 저장 + Shadow queue 등록
  → 답변은 분석을 기다리지 않고 진행
  → 별도 Worker가 pending TurnUnderstanding 처리
  → 결과는 audit DB에만 저장
```

## 안전 경계

- 기본 maintenance mode는 `shadow`다.
- 대화용 `WINTER_LLM_URL`을 분석용으로 암묵적으로 재사용하지 않는다.
- 실제 local backend는 전용 `WINTER_ANALYSIS_LLM_URL` 또는 `--analysis-url`이 없으면
  시작을 거부한다.
- 모델이 꺼져 있으면 pending event를 실패로 소진하지 않고 exponential backoff 뒤
  다시 시도한다.
- 같은 queue에 Worker가 두 개 뜨지 않도록 local file lock을 잡는다.
- 재시작해도 이미 completed인 event를 다시 분석하지 않는다.
- fake backend는 test와 명시적인 개발 probe에서만 선택할 수 있다. 운영 실패를 fake
  성공으로 숨기지 않는다.

## 개발자 실행

한 cycle만 진단할 때:

```bash
WINTER_ANALYSIS_LLM_URL=http://127.0.0.1:<전용-port> \
  python -m companion.maintenance_worker --once
```

계속 소비할 때:

```bash
WINTER_ANALYSIS_LLM_URL=http://127.0.0.1:<전용-port> \
  python -m companion.maintenance_worker
```

기본 batch는 5개, 빈 queue polling은 2초, 분석 runtime unavailable backoff는
5초에서 시작해 최대 60초다. SIGINT와 SIGTERM을 받으면 현재 cycle 경계에서 종료한다.

## 현재 배포 상태와 다음 gate

Worker 코드, single-instance, 재시작 무중복, unavailable backoff와 clean shutdown은
offline test로 검증했다. 2026-08-17 전체 회귀는 546개 test, Ruff 전체와 mypy 127개
source file을 통과했다. 그러나 안전한 전용 분석 runtime과 장시간 supervisor는 아직
운영 배포하지 않았다. 과거 구조화 분석 부하 중 Orin reset이 관찰됐기 때문에 대화용
Orin을 이 Worker에 연결하지 않는다.

상시 운영 gate는 다음과 같다.

1. 대화·Voice GPU와 자원을 다투지 않는 전용 local analysis runtime을 정한다.
2. supervisor 아래에서 재시작과 24시간 idle/burst 동작을 확인한다.
3. CLI, Web, Voice에서 천우가 별도 명령을 쓰지 않아도 queue가 줄어드는지 확인한다.
4. 분석 중에도 대화 첫 응답 latency가 악화되지 않는지 비교한다.
5. 그 뒤 Reflection/current-state와 Outcome을 각각 독립 gate로 연결한다.

평가 gate를 통과하기 전에는 Worker가 계속 돌아도 추론 결과를 active Memory로 승격하지
않는다.

## Issue #118 운영 시작점 — 2026-08-19

- production TurnUnderstanding queue는 원문을 읽지 않은 aggregate 기준 pending 196건이다.
- `127.0.0.1:18081` 전용 analysis listener와 maintenance user unit은 아직 없다.
- `127.0.0.1:18080`은 대화 경로이므로 명시·암묵적으로 재사용하지 않는다.
- GPU free 약 2.3 GiB 상태에서 두 번째 GPU model 상주를 가정하지 않는다.
- 이전 평가에 사용한 CPU-only Qwen3-4B runtime을 로컬에서 먼저 resolve하고 copied DB
  canary→bounded live batch→continuous Shadow 순서로만 연다.
- 로그에는 cycle별 processed/completed/failed/deferred와 queue depth·latency aggregate만
  남기며 user text, context와 model prompt를 출력하지 않는다.
- 24시간 관찰은 service 최초 배포 완료와 분리한다. 실제 시간이 지나기 전에 완료로
  기록하지 않는다.
