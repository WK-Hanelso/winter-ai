# STT 파일럿 — 구간 전사 품질 측정

수집한 오디오의 짧은 구간을 whisper.cpp로 전사하고, 그 결과가 `raw_transcript`로
쓸 만한지 측정하는 절차다.

관련: [model-selection.md](model-selection.md),
[reference-subtitle-quality.md](reference-subtitle-quality.md),
[reference-audio-ingest.md](reference-audio-ingest.md).

---

## 1. 정답이 없는 상태에서 무엇을 재는가

private source에는 정답 transcript가 없다. 따라서 **절대 정확도는 측정할 수 없다.**

대신 두 가지를 잰다.

1. **독립적인 두 ASR의 일치도.** 같은 구간에 대해 YouTube 자동 caption(`ko-orig`)을
   이미 확보했으므로(#82) whisper 출력과 직접 비교할 수 있다. 일치는 신뢰의 근거이고,
   불일치는 사람이 확인해야 할 구간을 표시한다.
2. **구어 형태의 보존.** 단어를 아무리 정확히 맞혀도 머뭇거림을 지워버리면
   `raw_transcript`가 아니라 `normalized_transcript`다.

## 2. 구현

whisper.cpp 공식 image 하나로 끝난다. **ffmpeg가 그 image에 이미 들어 있어서**
자르기·리샘플·전사를 같은 pinned runtime에서 수행하며 새 의존성이 없다.

whisper.cpp가 VTT를 출력할 수 있으므로([자막 품질 측정](reference-subtitle-quality.md)의
파서와 지표를 그대로 재사용한다. 두 전사가 같은 형식으로 비교된다.

### 주의점

- **`-ss`를 `-i`보다 앞에 둔다.** 그래야 ffmpeg가 디코딩 전에 seek한다. 28분 파일에서
  이 차이는 순간과 전체 디코딩 한 번의 차이다
- **`-ng`로 CPU 실행을 고정한다.** GPU로 조용히 넘어가면 측정한 소요 시간이 무의미해진다
- **`--user`를 반드시 준다.** 없으면 컨테이너가 출력물을 root 소유로 만들어 Host가
  방금 만든 transcript의 권한조차 바꾸지 못한다. 실제로 첫 실행에서 발생했다
- **원본은 source당 한 번만 staging한다.** 구간마다 staging하면 25 MiB 원본이 구간
  수만큼 복제된다. 실제로 그렇게 만들었다가 고쳤다
- 중간 WAV도 0600으로 되돌린다. 컨테이너가 기본 umask로 644를 남긴다

### 시간 기준 정렬

로컬 전사는 0초에서 시작하고 service caption은 source 절대 시간을 쓴다. 두 시계를
그대로 비교하면 안 되므로 `slice_cues()`가 구간을 잘라내며 segment 기준으로
rebase한다.

## 3. 실행

```bash
PYTHONPATH=src python3 experiments/reference_transcription_probe.py \
  --audio-path <외장>/raw/audio/<candidate>/<source>.webm \
  --reference-vtt <외장>/raw/subtitles/<candidate>/<source>.ko-orig.vtt \
  --model-path <모델>/ggml-small.bin \
  --work-dir <외장>/derived/audio/stt-pilot \
  --source-id <source-id> \
  --start-seconds 600 --duration-seconds 300
```

`stt_probe.py`와 같이 Host에서 실행하며 pinned image를 구동한다.

## 4. 측정 결과 (2026-08-09, source-003, whisper small, CPU)

두 구간을 측정했다. 첫 구간에는 약 1분 30초의 노래가 포함돼 대표성이 낮아
대화만 있는 구간을 추가로 측정했다.

| 구간 | 일치도 | 문자비 | 필러 잔존 | 소요 | 실시간 대비 |
| --- | ---: | ---: | ---: | ---: | ---: |
| 10:00~15:00 | 0.498 | 0.529 | 0.444 | 102.5초 | 2.93배 |
| 20:00~25:00 | 0.531 | 0.515 | 0.286 | 81.7초 | 3.67배 |

트랙별:

| 구간 | 트랙 | 문자 | cue | 커버리지 | 필러/1k자 | 부호/1k자 |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 10:00~15:00 | whisper | 1,176 | 62 | 0.938 | 20.41 | 50.17 |
| 10:00~15:00 | service | 2,112 | 197 | 0.953 | 25.57 | 0.00 |
| 20:00~25:00 | whisper | 861 | 51 | 0.596 | 9.29 | 73.17 |
| 20:00~25:00 | service | 1,551 | 137 | 0.789 | 18.05 | 0.00 |

### 판정

**whisper small은 현재 설정으로 `raw_transcript`를 만들지 못한다.** 두 가지가 겹친다.

1. **내용이 빠진다.** 두 구간 모두 문자 수가 service의 약 52%다. 노래가 포함된
   구간과 대화만 있는 구간에서 값이 거의 같으므로 노래 때문이 아니다. 20:00 구간에서는
   커버리지도 0.596 대 0.789로 낮아, 시간축에서도 빠뜨리고 있다
2. **구어를 다듬는다.** 필러 비율이 service의 절반이고, service에 전혀 없는
   문장부호를 1000자당 50~73개 붙인다

두 번째는 모델 크기 문제가 아닐 가능성이 크다. Whisper는 자막류 텍스트로 학습됐고,
자막은 #82에서 측정했듯 정확히 구어를 다듬은 형태다. **다듬는 것이 학습 목표에
내장돼 있다는 가설**이며, 이 측정만으로는 확정할 수 없다.

### 뒤집힌 결론

이 측정의 부수적 결과로 **YouTube 자동 caption(`ko-orig`)이 whisper small보다
`raw_transcript`에 가깝다**는 점이 드러났다. 필러 비율이 높고 문장부호가 전혀 없으며
커버리지도 더 넓다.

다만 자동 caption의 인식 정확도는 여전히 미측정이며 화자 구분도 없다.

## 5. 알려진 한계

- 정답이 없으므로 어느 쪽이 맞는지 판정할 수 없다. 일치·불일치만 잴 수 있다
- service caption의 문자 수가 rolling caption 중복 제거 후에도 과대 계상될
  가능성을 완전히 배제하지 못했다. prefix 형태가 아닌 중복은 걸러지지 않는다
- 두 구간, 총 10분만 측정했다
- whisper medium 이상은 비교하지 않았다. 모델을 내려받지 않았다
- **화자 구분은 어느 쪽에도 없다.** 이 source는 인터뷰이며 발화가 겹친다
- CPU 단일 설정만 측정했다. thread 수와 sampling 설정은 조정하지 않았다

## 6. 운영상 발견

whisper small checkpoint가 `/tmp` 아래에 있다. 재부팅 시 사라질 수 있는 위치이며
487 MiB를 다시 내려받아야 한다. 영구 경로로 옮기는 것을 권고한다.
