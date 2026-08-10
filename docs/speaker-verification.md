# 화자 검증 — 음성 결과

대화형 source에서 Reference 발화만 골라내려 한 시도와, **실패한 결과**를 기록한다.

관련: [reference-transcription-probe.md](reference-transcription-probe.md),
[ADR-0003](adr/0003-reference-transcription-strategy.md).

---

## 1. 접근

일반 diarization("몇 명이 언제 말했나")은 필요한 것보다 큰 문제다. 필요한 것은
"이 구간이 Reference인가" 하나이고, 단독 화자 source가 이미 있으므로 **검증**으로
풀 수 있다고 보았다.

- 단독 source의 앞 70%로 기준 임베딩(centroid)을 만든다
- 나머지 30%는 positive control — 전부 Reference여야 한다
- 대화형 source의 전사 cue마다 유사도를 재고, 두 무리로 갈리는지 본다

두 무리로 갈리지 않으면 임계값을 정할 수 없다. 그 경우 **임계값을 내지 않고 실패로
보고**하도록 만들었다.

## 2. 결과 — 갈리지 않았다

| 대상 | 구간 | 평균 | 편차 | 이중분포 |
| --- | ---: | ---: | ---: | --- |
| positive control (단독 화자 held-out) | 132 | 0.488 | 0.214 | 예 (0.05 / 0.57) |
| **대화형 source 5분** | 62 | **0.372** | 0.180 | **아니오** |

대화형 source의 유사도는 **하나의 무리**로 나왔다. 가장 낮은 1개를 제외하면 나머지
61개가 평균 0.377에 모여 있다. 두 화자가 구분되지 않았다.

### positive control도 깨끗하지 않았다

단독 화자 구간인데도 **132개 중 20개가 0.05 근처**로 나왔다. 같은 사람의 목소리인데
거의 무관하다고 판정된 것이다. 음악·무음·잡음이 담긴 창으로 보인다.

즉 **전사 cue의 약 15%는 사람 목소리가 실질적으로 없는 구간**이다.

## 3. 왜 실패했나

대화형 source의 분포가 control의 "잡음 무리"(0.05)와 "목소리 무리"(0.57) **사이**에
통째로 놓여 있다. 두 무리로 갈리는 대신 중간에 뭉쳤다.

가장 그럴듯한 설명은 **전사 cue가 화자 단위가 아니라는 것**이다. 실제 전사에서 한
cue 안에 두 사람의 말이 함께 나타나는 것을 이미 확인했다. 두 목소리가 섞인 창은
중간값 임베딩을 만들고, 그것이 전체를 단봉으로 만든다.

녹음 조건 차이(음성 전용 방송 대 스튜디오 인터뷰)도 대화형 source 전체의 유사도를
낮추는 방향으로 작용했을 수 있다. 이 측정만으로는 두 요인을 분리할 수 없다.

### 확인해서 배제한 것

임베딩을 정규화하지 않고 평균 낸 것이 원인일 수 있어 고쳤다. 코사인 유사도는 방향만
보므로 정규화 전 평균은 큰 벡터에 끌린다.

**결과는 0.4884 → 0.4881로 거의 변하지 않았다.** 구현상 옳은 수정이지만 실패의
원인은 아니었다.

## 4. 결론

**전사 cue 단위 화자 검증은 이 자료에서 동작하지 않는다.** 화자 경계와 전사 경계가
일치하지 않기 때문이다.

이는 지름길이 막혔다는 뜻이며, 다음 중 하나가 필요하다.

- 화자 전환 지점을 직접 찾는 segmentation (즉 본래 의미의 diarization)
- VAD로 발화 구간을 잡고 그 안에서 고정 길이 창으로 다시 자르기
- cue 안을 여러 창으로 나눠 최대·중앙 유사도를 쓰는 방식

세 번째가 가장 싸지만, 겹쳐 말하는 구간은 어느 방법으로도 확정할 수 없다.

## 5. 실행

```bash
docker compose -f compose.reference-source.yaml run --rm reference-speaker-probe \
  --enrolment-audio <외장>/derived/.../<solo>-0-<duration>.wav \
  --enrolment-vtt   <외장>/derived/.../<solo>-0-<duration>.vtt \
  --target-audio    <외장>/derived/.../<target>.wav \
  --target-vtt      <외장>/derived/.../<target>.vtt \
  --target-source-id <source-id>
```

## 6. 구축 과정에서 걸린 것

image를 만들며 네 번 막혔다. 재현을 위해 기록한다.

| 증상 | 원인 |
| --- | --- |
| `No module named 'requests'` | `huggingface_hub` 1.x가 requests를 버렸는데 `speechbrain` 1.0.2는 직접 import한다. 0.26.5로 고정 |
| torchaudio backend 없음 | `soundfile` 미설치. libsndfile1만으로는 부족하다 |
| 모델 파일 읽기 거부 | speechbrain이 savedir을 root 전용 HF 캐시로의 심볼릭 링크로 채운다. 실제 파일로 역참조 |
| 오프라인에서 Hub 조회 실패 | `hyperparams.yaml`이 파일을 Hub 저장소 id로 참조한다. 캐시를 이미지에 함께 넣고 실행 시 쓰기 가능한 위치로 복사 |

`pyannote` 대신 `speechbrain`을 쓴 이유는 pyannote가 Hugging Face 토큰과 약관 동의를
요구하기 때문이다. 외부 계정 없이는 돌릴 수 없는 probe는 재현 가능하지 않다.

## 7. 알려진 한계

- 대화형 source 5분 한 구간, 단독 source 하나로만 측정했다
- 겹쳐 말하는 구간은 어떤 방법으로도 확정할 수 없다
- 녹음 조건 차이와 cue 혼입을 분리하지 못했다
- 임계값을 정하지 못했으므로 발화 추출은 아직 불가능하다
