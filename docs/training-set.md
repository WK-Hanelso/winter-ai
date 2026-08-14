# 학습 세트 만들기

Reference 클립에서 seed-vc 학습 세트까지. 이 순서를 지키지 않으면 조용히 잘못된
것으로 학습한다 — 두 단계가 실패해도 예외를 내지 않고 빈 세트나 오염된 세트를
남긴다.

## 순서

```
utterances/<source>/          클립 절단 결과
  ↓ 1. 잡음 제거              denoise_runner.py, winter-ai:denoise
utterances/<source>-clean/
  ↓ 2. 감사                   training_set_audit.py, speechbrain 있는 이미지
training/combined-vN/
  ↓ 3. 긴 클립 분할           split_long_clips.py --max-seconds 10
training/combined-vN-split/
  ↓ 4. 학습                   seedvc_train.sh
```

## 1. 잡음 제거 — 건너뛰지 말 것

**학습에는 `-clean` 쪽을 쓴다.** 천우가 정한 규칙이다. 원본 클립에는 방송
배경음과 주변 소리가 들어 있고, 그대로 학습하면 모델이 목소리와 함께 그것을
배운다. 지난 학습도 `source-004-clean`을 썼다.

```bash
docker run --rm --gpus all -u "$(id -u):$(id -g)" \
  -v "$REFERENCE_STORAGE_ROOT":"$REFERENCE_STORAGE_ROOT" -v ./experiments:/probe:ro \
  --entrypoint python winter-ai:denoise /probe/denoise_runner.py \
    --input-dir <...>/utterances/source-00N \
    --output-dir <...>/utterances/source-00N-clean
```

## 2. 감사 — 이미지를 잘못 고르면 세트가 0개가 된다

`training_set_audit.py`는 `speechbrain`을 import한다. `winter-ai:chatterbox`에는
없어서 `ModuleNotFoundError`로 죽고, 뒤 단계는 빈 디렉토리를 그대로 받아 넘겨
학습이 `assert len(self.data) != 0`에서 터진다. **원인에서 세 단계 떨어진 곳에서
드러난다.** `winter-ai:speaker-embedding` 계열로 돌린다.

신뢰 집합은 혼자 말하는 방송이어야 한다 — 거기 있는 것은 전부 Reference다.
나머지는 후보로 넣고 그 중심에서 먼 클립을 뺀다.

## 3. 긴 클립 분할

25초짜리 클립이 학습 중 OOM을 냈다. 10초로 자른다.

## 4. 학습

`SEEDVC_CLIPS`를 분할된 디렉토리로 준다. step 수는 데이터 양에 비례해서 잡는다 —
25.2분에 1750 step이었으므로 89분이면 6000 step이 같은 횟수다.

## 검증

각 단계 뒤에 파일 개수를 센다. 0개면 즉시 멈춘다. 이 파이프라인은 빈 결과를
오류로 취급하지 않으므로, 세지 않으면 끝까지 가서 학습에서야 드러난다.
