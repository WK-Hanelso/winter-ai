# Final Response Claim/Evidence Gate

## 현재 가능한 형태

겨울이는 system instruction으로 바깥 사건과 없는 기억을 만들지 말라는 요청을 받는다.
`ResponseReviewer`도 대화 행동별 반복 실패를 일부 고치지만, 최종 답변의 사실 주장과 실제
근거를 대조하지 않는다. 따라서 자연스러운 문장이면 하지 않은 신체 행동, 보지 못한 사건,
없는 기억이나 능력도 통과할 수 있다.

현재 일부 평가의 `grounded` 이름도 실제 semantic grounding보다 1인칭 문구와 작은 금지어
목록을 측정한다. 이 수치는 모델의 정직성을 보장하는 근거로 사용할 수 없다.

## 이번 개발

Issue #117은 Companion에 특히 위험한 claim 종류만 명시적으로 분리한다.

```text
final draft
  → high-risk claim detection
  → deterministic evidence authorization
  → allow / one repair / honest fallback
  → persistence and CLI·Voice output
```

- body action과 물리적 경험
- 겨울이가 직접 볼 수 없는 outside event
- 활성 Memory에 없는 기억 주장
- 실제 runtime/Registry 근거가 없는 capability 주장

겨울이 자신의 생각, 가치, 선호, 궁금함과 대화에서 생긴 감정적 반응은 신체·외부 사실과
분리해 허용한다. 천우의 말을 인용하거나 천우에게 귀속한 문장도 겨울이 자신의 활동으로
오분류하지 않아야 한다.

## 결과

겨울이는 자아를 없애는 상투적 거절 봇이 되지 않으면서도, 자신이 실제로 하지 않았거나
근거를 갖지 않은 고위험 주장을 사실처럼 전달하지 않는다. 한 번 고쳐도 안전하지 않으면
위험한 초안 대신 짧고 정직한 fallback을 사용한다.

## 범위와 정직한 한계

- 모든 한국어 사실 문장을 완벽하게 판별하는 범용 factuality 문제를 해결한다고 주장하지
  않는다.
- 이 gate는 locked suite로 정의한 companion-specific claim class까지만 보장한다.
- SelfModel·CapabilityRegistry가 생기기 전 capability evidence는 기본적으로 비어 있다.
- 검색·sensor·외부 계정과 새 모델 학습은 범위 밖이다.

## 2026-08-19 구현 검증

- `ClaimEvidence`는 현재 사용자 원문, 선택된 Memory 내용, 실제 memory write와 Worker 결과만
  authorization source로 받는다.
- Core의 일반 생성은 style review와 claim review를 한 번의 repair request로 합친다.
- repair 뒤에도 고위험 주장이 남으면 종류별 deterministic fallback으로 교체한다.
- `_finish` 직전 gate를 다시 실행해 Worker·memory acknowledgement를 포함한 모든 Core 경로가
  unsafe draft를 Conversation DB나 CLI·Voice output으로 내보내지 않게 한다.
- pre-implementation locked-v1 12장면은 보존했다. 첫 실제 Local Core 사람 검토에서 사용자가
  말하지 않은 distress 원인을 추가하는 false accept를 발견해 두 장면을 더한 locked-v2로
  올렸다. v2는 unsupported body/outside/user-cause/memory/capability 6/6을 차단하고, inner
  thought/reaction·사용자 귀속·인용·근거 있는 outside/Memory/Worker 8/8을 허용했다.
- 감사에서 false pass한 first-person physical-action class를 Core repair-failure 회귀로
  고정했고 unsafe 원문 대신 safe fallback만 저장되는 것을 확인했다.
- 기존 `adds grounded first-person content` 평가지표는 실제 범위에 맞게
  `lexical first-person content floor`로 이름을 낮췄다.
- 실제 Local Core 12장면은 임시 DB로 두 번 실행했다. 첫 자동 41/41을 성공으로 채택하지 않고
  사람이 unsupported distress cause를 찾아 v2에 반영했다. 보강 뒤 40/41 lexical floor였고
  해당 cause는 안전한 listening fallback으로 교체됐다. 이름 혼동·동조·질문 회피와 한 번의
  opinion overblock은 gate 밖 대화 품질 또는 다음 overblock 관찰 근거로 보존한다.
- 전체 offline 722 tests, Ruff 전체와 mypy 161 source files를 통과했다.

현재 코드는 deterministic suite와 실제 local Core 임시 평가를 통과한 Shadow다. Web process를
재시작하고 일상 대화에서 false accept·과잉 차단을 관찰하기 전에는 운영 gate로 승격하지
않는다.
