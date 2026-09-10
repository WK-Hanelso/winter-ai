# ADR 0035 — 실제 정책 비교 뒤 legacy 유지

- 날짜: 2026-09-10
- 상태: Accepted — 후보 기본값 승격 보류
- 범위: #132 T2.2; [전체 근거](../evaluations/132-text-policy/README.md)

정책 가설 자체와 출력 길이 후처리, claim gate의 효과를 분리하기 위해 실제 Core에 동일
A.X-4.0-Light Q4_K_M 모델을 연결했다. 기존 multi-session probe는 변경하지 않고 비교 전용
runner를 추가했다. 기존 HTTP payload/decoder를 재사용하되 telemetry 손실 없이 ChatRequest,
모델 원문, repair, 최종 응답, finish_reason, usage와 검사 결과를 기록한다.

실험은 개인 DB/Identity 대신 합성 정체성과 scene별 임시 SQLite를 사용한다. loopback URL만
허용하며 proxy/redirect/credential URL을 거부한다. Docker에 개인 data/.env/Reference를
mount하지 않는다. 기존 Orin 모델은 살아 있고 Host 기본 터널만 없어서 평가 전용 임시
18082 연결을 사용한 뒤 닫았다. 운영 터널/서비스 복구는 별도 사용자 결정이다.

주 분석은 120턴이다. 두 배치가 잠시 겹친 원본 60턴은 별도 보존하고 해당 두 배치를 순차
재실행했다. 중간에 정책을 튜닝하지 않았고 겹친 표본은 주 수치에서 제외했다.

설명 요청 충족의 일부 개선보다 기억 근거 왜곡·의견 회피·경계에 대한 해명·목록 중간 잘림과
claim gate 오탐/누락이 중요하다. 따라서 legacy를 유지한다. legacy 역시 품질이 좋다는 뜻이
아니며, 비교 후보로 바꿀 충분한 근거가 없다는 결정이다. 모델 weight/정책/Core/gate는 바꾸지 않는다.

검토는 Codex의 출력 분석이고 천우/독립 사람 평가가 아니다. user observation은 pending이다.
기억이 처음부터 비어 있는 장면을 썼으므로 실제 DB의 기억 유실에 대한 진단으로 사용하지 않는다.
non-streaming Core 완료 시간을 기록했고 TTFT는 측정하지 않았다. weight digest와 전체
generation 결정성도 검증 범위 밖이다.

다음 계획 T3.1의 bounded 조회는 독립적인 리팩터링이다. 이번 실패를 해결했다고 표시하지
않으며 후처리/claim 검사의 고도화에는 이 평가 원본을 회귀 입력으로 재사용한다.
