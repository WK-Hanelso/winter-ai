# ADR-0022: Evidence-gated Capability Registry와 read-only SelfModel

- 상태: Accepted
- 날짜: 2026-08-19
- Issue: #119

## 문제

겨울이는 Identity와 Memory를 지속해서 사용하지만, 자신이 **실제로 지금 할 수 있는 일**은
prompt의 설명에 의존했다. 이 방식은 runtime에 연결되지 않은 기능을 약속하거나, 반대로 이미
검증된 기능도 부정하는 문제를 막을 수 없다. 모델이 만든 capability label은 실행 권한의
근거가 될 수도 없다.

## 결정

별도 SQLite `CapabilityRegistry`를 두고 기능 정의, version, 지원 범위, limitation,
machine-readable claim scope, dependency, health·test/evaluation evidence와 lifecycle 전이를
지속한다.

```text
proposed → approved → building → testing → available
                                      └──→ failed
available → degraded → available / failed
available 또는 진행 상태 → retired
```

`available` 전이는 다음을 모두 요구한다.

1. 가장 최근 health evidence가 성공이다.
2. test 또는 evaluation 성공 근거가 하나 이상 있다.
3. 모든 dependency가 `available`이다.

각 대화 turn 시작 시 `PersistentSelfModel`이 approved Identity와 Registry를 읽어 immutable
snapshot을 만든다. Registry가 `available`이어도 현재 interface runtime에 실제 연결되지 않은
기능은 그 snapshot에서 `degraded`로 본다. snapshot의 claim scope만 final response claim
gate에 전달한다. 이 조회는 capability 상태를 바꾸지 않는다.

기존 기반 기능인 현재 대화와 승인된 Memory는 별도 trusted bootstrap이 실제 local model
health, SQLite open, 전체 회귀 test reference를 확인한 뒤에만 등록한다. 천우의 정상 사용
흐름에는 설치·정리 명령을 요구하지 않는다. `./winter capabilities`는 개발·audit용 읽기
명령이다.

## 경계

- claim scope는 실행 권한이 아니다. 말할 수 있는 범위만 제한한다.
- Registry 항목 생성·상태 전이는 모델 응답이나 Belief 자동 activation으로 수행하지 않는다.
- Capability 채택, Worker 실행, 계정 권한과 외부 write는 이 Issue에 포함하지 않는다.
- `adopted_change`와 `rollback_target` 필드는 이후 채택 cycle을 위한 계약일 뿐, 현재 기반
  기능의 자동 채택을 의미하지 않는다.
- unavailable 기능을 감추지 않고 limitation과 함께 SelfModel에 제공한다.

## 결과

CLI, Web, Voice가 같은 `capabilities.sqlite`와 `CompanionCore`를 사용한다. 겨울이는 검증된
대화·Memory 능력은 자기 능력으로 설명할 수 있지만, 파일 수정·검색·자율 작업처럼 등록되지
않은 기능은 가능한 것처럼 주장할 수 없다. 이후 Worker capability는 같은 Registry와 별도
채택 승인 계약을 통과해야 한다.

## 남은 검증

- 실제 자유 대화에서 capability 질문의 자연스러움과 과잉 차단을 관찰한다.
- runtime 장애를 자동 health evidence와 `degraded` 전이로 연결한다.
- Improvement Ledger와 adoption/rollback evidence를 연결한다.
- Voice·Web·CLI 교차 사용에서 같은 snapshot이 유지되는지 장시간 평가한다.
