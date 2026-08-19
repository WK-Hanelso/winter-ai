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
- transient timeout row는 `defer_count`와 시각을 남기고 defer가 적은 pending 뒤로 회전한다.
  같은 row가 queue 선두를 영구 점유하지 않으며 삭제·failed 전환도 하지 않는다.
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
5초에서 시작해 최대 60초다. 운영 unit은 대화 여유를 위해 batch 1과 완료 후 5초 휴식을
명시한다. SIGINT와 SIGTERM을 받으면 현재 cycle 경계에서 종료한다.

각 처리 cycle은 사용자 원문 없이 `processed/completed/failed/deferred`, 처리 전후 pending
수와 elapsed만 기록한다. queue 수는 SQL `COUNT`로 읽어 private text를 계측 경로에 올리지
않는다.

## 현재 배포 상태와 다음 gate

Worker 코드, single-instance, 재시작 무중복, unavailable backoff와 clean shutdown은
offline test로 검증했다. 2026-08-19에는 전용 CPU runtime과 user systemd supervisor를
초기 배포했다. 대화용 Orin `:18080`과 Voice GPU는 사용하지 않는다.

상시 운영 gate는 다음과 같다.

1. supervisor 아래에서 재시작과 24시간 idle/burst 동작을 확인한다.
2. CLI, Web, Voice에서 천우가 별도 명령을 쓰지 않아도 queue가 줄어드는지 확인한다.
3. 분석 중에도 실제 대화 첫 응답 latency가 악화되지 않는지 더 긴 표본으로 비교한다.
4. 처리 결과를 사람 audit해 기존 TurnUnderstanding 5/10 실패 축을 다시 측정한다.
5. 그 뒤에만 Reflection/current-state와 Outcome을 각각 독립 gate로 연결한다.

평가 gate를 통과하기 전에는 Worker가 계속 돌아도 추론 결과를 active Memory로 승격하지
않는다.

## Issue #118 초기 배포 — 2026-08-19

- model: `Qwen3-4B-Instruct-2507.Q4_K_M.gguf`, SHA-256
  `edabe01d973c31dce0d71eaf7e44628021b23b9bd2cbb93059846dad1cc4e153`
- runtime: official llama.cpp tag `b10276`, commit
  `6ea215d171fd31df943bf1ac8227129f2b963160`, CPU-only API build. host CMake 3.16에서
  disabled UI asset script만 최소 version 3.16으로 낮췄으며 UI·download 경로는 모두 껐다.
- endpoint: `127.0.0.1:18081`, 4 CPU threads, parallel 1, context 2048. GPU layer는 빌드
  자체에 없고 Web UI와 prompt log도 껐다.
- supervisor: `winter-analysis.service`, `winter-maintenance.service`. worker는 batch 1,
  완료 뒤 5초 휴식, idle 10초, unavailable backoff 최대 120초, low CPU/IO priority다.
  stop/restart는 현재 최대 요청보다 긴 130초를 기다려 cycle 경계에서 끝낸다.
- copied production DB canary 5/5 completed, failed 0. 2-thread 첫 건은 91.59초,
  4-thread 3건 연속은 126.21초(건당 42.07초)였다.
- 동일 synthetic 대화 모델 요청 3회의 평균은 analysis idle 0.300초, 부하 중 0.285초였다.
  작은 표본에서 악화가 보이지 않았다는 뜻일 뿐 실사용 무영향 확정은 아니다.
- bounded live canary 1건은 pending `196→195`, completed 1, failed 0, 59.9초였다.
  active Memory는 전후 동일한 10건이며 분석 결과는 답변·Memory에 연결되지 않았다.
- 두 user service는 자동 시작 상태다. 24시간 supervisor/queue/대화 latency와 결과 품질
  사람 audit은 아직 끝나지 않았다.
- 배포 직후 전체 offline 회귀는 724 tests, Ruff 전체, mypy 161 source files를 통과했다.
- live restart는 진행 중 cycle을 67.0초에 완료해 pending `190→189`로 한 번만 전이한 뒤
  정상 종료·새 PID 기동했다. 강제 종료, failed event와 중복 완료는 없었다.

### 운영 관찰 snapshot — 2026-08-19 16:09 KST

- aggregate queue는 `completed 16 / failed 8 / pending 175`다. 최근 처리 24건 기준 성공
  16건·schema/grounding 차단 8건으로, 초기 canary의 failed 0은 실제 다양성에서 유지되지
  않았다.
- 실패는 exact evidence substring 불일치 6건(`observations` 4, intent 1, affect 1)과
  confidence 형식 오류 2건이다. invalid JSON이나 runtime 장애를 fake 성공으로 바꾼 결과는
  없다.
- 한 요청이 120초를 넘겨 deferred됐지만 pending으로 남아 재시도된다. schema/grounding
  실패 8건은 failed audit row로 보존되며 Memory나 답변으로 전달되지 않는다.
- `winter-analysis`는 initial hardening 호환 수정 전 restart 4회 뒤 15:32부터 active이며,
  `winter-maintenance`는 restart 0회로 15:40부터 active다. 확인 시점 analysis health는
  정상이었다.
- active Memory는 여전히 10건이다. 이 결과는 기존 TurnUnderstanding 5/10 activation 실패를
  운영 데이터가 다시 지지하므로, prompt 예외나 validator 완화로 통과시키지 않는다.

따라서 배포 안정성 관찰은 계속하되 결과 소비자 연결 gate는 실패 상태다. 24시간이 끝나도
이 실패율과 사람 품질 audit가 해결되지 않으면 service 생존과 분석 결과 activation을 별도로
판정한다.

### Deferred starvation 보강 — 2026-08-19 16:35 KST

- 16:31 snapshot에서 한 row가 120.1초 timeout으로 최소 8회 연속 deferred돼 뒤의 pending
  177건을 막는 head-of-line starvation을 확인했다.
- schema v3는 pending row마다 `defer_count`, `last_deferred_at`을 additive column으로 보존한다.
  선택 순서는 defer가 적은 row를 우선하므로 timeout row는 pending인 채 뒤로 회전한다.
- 두 row deterministic 회귀에서 첫 row defer 뒤 두 번째 row가 다음 cycle에 완료됐고, 재시작
  뒤 첫 row의 defer count와 pending 상태가 유지됐다.
- 운영 DB 복사본 migration 전후 집계는 completed 16·failed 8·pending 177로 동일했다.
- 집중 37 tests, 전체 749 tests, Ruff 전체와 mypy 165 source files를 통과했다.

운영 DB migration과 실제 timeout row 뒤의 다음 row 진행을 확인하기 전에는 배포 완료로
판정하지 않는다.

개발자가 상태만 확인할 때:

```bash
systemctl --user status winter-analysis.service winter-maintenance.service
journalctl --user-unit winter-maintenance.service --since today
```

정상 사용자 흐름에서는 이 명령이 필요 없다.
