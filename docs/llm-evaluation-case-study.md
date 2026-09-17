# LLM Held-out Evaluation and Failure Analysis

## 왜 held-out 평가가 필요했는가

prompt와 말투 profile을 조정한 뒤 같은 transcript로 다시 확인하면 관찰한 문장을 되풀이해
좋아 보일 수 있다. 그래서 대화 pair를 기록 순서대로 앞 50%와 뒤 50%로 나누고, 뒤쪽
prompt에 local LLM이 낸 답을 실제 held-out response와 비교했다. 이 평가는 “좋은 답인가”가
아니라 한 개의 기록된 wording에 얼마나 가까운가를 묻는다.

## 1차 — held-out 6쌍에서 판단 보류

첫 dataset은 전체 11쌍, held-out 6쌍이었다. `reference_broadcast`의 chance-relative
position은 0.021이었지만 표본이 너무 작아 모델이나 profile을 판단하지 않았다. 작은
숫자에 의미를 붙이는 대신 pair 생성 범위를 먼저 넓혔다.

## 2차 — held-out 20쌍과 chance baseline

평가용 대화쌍을 전체 40개로 늘려 held-out 20개를 평가했다. `reference_broadcast`의
chance-relative position은 0.105였다. 이때 함께 보는 지표의 의미는 다음과 같다.

- `similarity`는 정규화된 generated response와 recorded response 문자열의 순서 기반
  유사도다.
- `token_overlap`은 recorded response의 고유 token 중 generated response에도 나타난
  비율이다. 긴 답이 우연히 유리할 수 있으므로 단독 판정에 쓰지 않는다.
- chance baseline은 각 held-out response를 바로 다음 held-out response와 비교한 평균이다.
  모든 조합 평균이나 무작위 반복은 아니다.
- chance-relative position은 generated similarity가 chance baseline에서 exact-match
  upper bound 1.0 쪽으로 이동한 상대 위치다. lower clamp가 없어 chance보다 낮으면
  음수가 될 수 있고, chance가 1에 가까우면 정의할 수 없다. accuracy나 response quality가
  아니다.

## 질문 fragmentation 발견

낮은 평가 점수를 바로 모델 성능 문제로 판단하지 않고, 입력 데이터와 pair 구성부터 다시
확인했다. word-level transcript에서 짧은 미배정 구간이 같은 화자의 turn을 끊었고,
Reference response 직전의 마지막 조각만 prompt가 되는 경우가 있었다. 2차 dataset의 질문
평균 길이는 6.67단어였다.

## 3차 — held-out 21쌍

같은 speaker turn 사이의 짧은 미배정 gap을 잇는 `bridge_short_gaps()`를 pair 생성 전에
적용했다. gap text 자체는 넣지 않으며, 서로 다른 speaker나 긴 gap은 합치지 않는다.
평가용 대화쌍은 전체 42개, held-out은 21개가 됐고 질문 평균 길이는 8.88단어로 늘었다.
`reference_broadcast` chance-relative position은 0.105에서 0.157로 올라갔다.

## 결과 변화와 해석 범위

세 round의 전체 pair 수는 11→40→42, held-out 수는 6→20→21이고,
`reference_broadcast` chance-relative position은 0.021→0.105→0.157이었다. 2차와 3차
사이에는 pair 증가와 short-gap bridge가 함께 포함돼 있어 상승분을 fragmentation 수정의
단독 효과로 분리할 수 없다. 다만 input fragmentation이 평가 결과에 영향을 준 요인 중
하나였다는 근거는 얻었다.

현재 평가는 source와 model이 제한되고, 질문마다 정답이 하나뿐이며, 앞선 대화 맥락도
제공하지 않는다. 시간순 split과 train/held-out exact pair overlap 실행 검사는 적용하지만
source/date grouping과 semantic near-duplicate 검사는 아직 없다. 따라서 0.157은 모델의
정확도나 품질 점수가 아니라, 현재 pair 구성과 metric 아래에서 chance baseline과
exact-match upper bound 사이에 놓인 상대 위치다.
