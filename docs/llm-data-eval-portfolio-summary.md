# LLM Data and Evaluation Summary

## Problem

실제 사람의 대화와 음성을 참고하는 로컬 Personal AI에서는 원본의 저장 경계와 측정값의
신뢰성이 먼저 필요했습니다. 잘못 만든 pair와 중복 자막은 작은 평가를 크게 왜곡합니다.

## What I built

private manifest, metadata preflight, 외장 storage, subtitle/STT probe, diarization, pair
construction과 시간순 평가를 이었습니다. public/private report를 나누고 audio에는
SHA-256과 media metadata를 남겼습니다.

## Unexpected failure

자막 품질을 확인하던 중 rolling caption overlap 때문에 기존 parser가 집계한 5,669자 중
37.4%가 중복으로 들어간 문제를 발견했습니다. 수정 후 집계는 3,547자였고, 사람이 만든
자막이 더 짧다는 초기 결론도 반대로 바뀌었습니다.

## How I evaluated it

전체 11/40/42개 pair 중 시간순 6/20/21개를 held-out으로 평가했습니다. generated와
recorded response를 비교하고, 다른 recorded response끼리의 유사도를 chance baseline으로
뒀습니다. 말투는 앞 70%로 profile을 만들고 뒤 30%와 distribution distance를 비교했습니다.

## What changed after measurement

낮은 응답 값을 모델 성능으로 결론 내리지 않고 pair 생성을 확인했습니다. 짧은 공백에서
질문이 잘리는 문제를 고친 뒤 평균 길이는 6.67에서 8.88단어, chance-relative position은
0.105에서 0.157로 바뀌었습니다. pair 수도 함께 늘었으므로 단독 인과가 아니라 input
fragmentation이 한 요인이었다고 기록했습니다.

## What I did not do

SFT, LoRA, voice cloning은 시작하지 않았고 source/date grouping과 formal contamination
detector도 없습니다. 결과는 training보다 prompt, 입력 데이터와 평가 방법을 먼저 나누는
decision gate로 사용합니다.
