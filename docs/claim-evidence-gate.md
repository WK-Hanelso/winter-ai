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
