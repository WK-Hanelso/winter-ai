# Winter Web Turn Safety

## 현재 가능한 형태

폰 Web은 한 요청 안에서 STT, Shared Core, 문장별 TTS와 SSE 재생을 연결한다. 그러나 현재
lock은 음성 생성 구간만 감싼다. 따라서 거의 동시에 들어온 두 요청은 Shared Core를 함께
실행할 수 있고, background Core thread가 실패해도 caller가 그 예외를 받지 못한다.

그 결과 실제로는 실패했는데 빈 `done`이 전달되거나, 두 turn의 대화·기억 후처리 순서가
사용자가 말한 순서와 달라질 수 있다.

## 이번 개발

Issue #115는 한 Web turn 전체를 다음과 같은 단일 경계로 만든다.

```text
request
  → Shared Core 생성과 저장
  → 문장/TTS 전달
  → 성공 done 또는 명시적 error
```

- 먼저 들어온 turn이 끝날 때까지 다음 turn은 Shared Core를 시작하지 않는다.
- Core background thread의 예외를 caller로 전달한다.
- Core 또는 TTS가 실패하면 `done`을 내보내지 않고 SSE `error`로 끝낸다.
- 이미 시작된 Core는 request delivery가 실패해도 join해 저장 경계를 불명확하게 남기지 않는다.

## 결과

천우가 빠르게 두 번 누르거나 여러 Web 요청이 겹쳐도 겨울이는 한 대화를 끝낸 뒤 다음
대화를 처리한다. 겨울이가 답을 만들지 못한 경우에는 빈 답변을 정상 완료처럼 보이지 않고
화면에서 실패를 알 수 있다. 천우가 복구 명령을 실행하거나 내부 상태를 정리할 필요는 없다.

## 범위 밖과 고도화

- 이 작업은 요청을 직렬화한다. queue 위치·대기시간 표시는 이후 실제 불편이 측정될 때 붙인다.
- 이미 생성한 일부 audio 뒤 Core가 실패할 수 있으며, 그 경우 terminal event는 error다.
- client disconnect cancellation, model retry, true sentence streaming, VAD와 barge-in은 별도다.
- Memory 분석 정확도와 Worker 권한은 변경하지 않는다.

## 검증 계약

- Core 실패 시 exception이 caller와 SSE error까지 도달하고 `done`은 없다.
- 두 동시 turn에서 Core 최대 동시 실행 수는 1이다.
- TTS/delivery 실패 뒤 `done`은 없다.
- 정상 text/voice 경로 회귀가 유지된다.

## 2026-08-19 구현 검증

수정 전 regression은 Core 예외가 caller에 전달되지 않고 Web이 빈 `done`을 내보내며, 첫
turn을 의도적으로 정지한 동안 두 번째 Core가 진입하는 것을 재현했다. 수정 뒤에는 다음을
확인했다.

- Core 예외 direct propagation과 `done` 미발행
- 실제 HTTP handler의 SSE `error`와 빈 `done` 미발행
- 두 concurrent answer의 Core 최대 동시 실행 수 1
- TTS 실패 시 `done` 미발행
- Web 관련 정상·ingress·metrics 13 tests 통과
- 전체 offline 691 tests, Ruff 전체, mypy 161 source files 통과

코드는 deterministic failure/concurrency contract를 통과한 Shadow다. 실행 중인 user service가
새 process로 재시작되기 전에는 운영 경로에 반영됐다고 판정하지 않는다.
