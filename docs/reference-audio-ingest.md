# Reference 오디오 수집

승인된 source의 **오디오만** 외장 storage에 수집하는 절차다. 이 저장소에서 실제
media를 저장하는 유일한 경로이므로 계약을 좁게 잡는다.

관련: [reference-data-storage.md](reference-data-storage.md),
[reference-source-probe.md](reference-source-probe.md),
[reference-subtitle-quality.md](reference-subtitle-quality.md).

---

## 1. 범위

- **한 번에 source 하나.** `--source-id`로 명시해야 한다
- **오디오만.** 영상은 받지 않는다
- **재인코딩하지 않는다.** 받은 스트림을 그대로 저장한다

## 2. 설계 결정

### 2.1 한 번에 하나만

`--candidate-id`와 `--source-id`를 모두 요구한다. candidate 전체를 한 번에 받는
편의 기능은 두지 않았다. 실수의 비용을 파일 하나로 제한하기 위해서다.

이미 파일이 있으면 덮어쓰지 않고 실패한다. 재수집이 필요하면 사람이 먼저 지운다.

### 2.2 재인코딩 금지

probe image에는 ffmpeg가 없다. 이는 결손이 아니라 선택이다.

- Reference Voice 기준선이 목적이므로 원본 음질을 손실 압축으로 다시 깎으면 안 된다
- `--extract-audio` 같은 post-processing은 ffmpeg를 요구한다. `-f bestaudio`는
  요구하지 않는다
- 형식 변환이 필요하면 별도 단계에서 명시적으로 한다

결과적으로 container는 서비스가 주는 대로 `.webm`(opus) 또는 `.m4a`(AAC)가 된다.
`ALLOWED_AUDIO_SUFFIXES` 밖의 container가 오면 실패한다.

### 2.3 provenance 기록

파일만 남기면 나중에 "이게 어디서 온 무엇인지" 알 수 없다. 저장과 동시에 기록한다.

- SHA-256 — 이후 단계에서 같은 파일인지 확인할 수 있다
- byte size, format id, codec, sample rate, bitrate
- private manifest `local_paths`에 상대 경로 등록

## 3. 실패 정책

| 상황 | 동작 |
| --- | --- |
| 같은 source의 파일이 이미 있음 | 실패. 덮어쓰지 않는다 |
| candidate·source 미등록 | 실패 |
| yt-dlp가 0건 또는 2건 이상 보고 | 실패. 단일 스트림만 허용 |
| container가 허용 목록 밖 | 실패 |
| audio codec이 `none` | 실패 |
| 파일이 비었거나 duration 대비 비정상적으로 작음 | 실패. 잘린 다운로드나 오류 페이지를 성공으로 받지 않는다 |
| 오디오 외 파일이 생성됨 | 실패 |
| 저장 경로가 storage root를 벗어남 | 실패 |

`--expected-duration-seconds`를 주면 최소 크기 검사가 활성화된다. #77 probe의
duration을 그대로 넣는다.

## 4. 실행

```bash
docker compose -f compose.reference-source.yaml run --rm reference-audio-ingest \
  --candidate-id <candidate-id> \
  --source-id <source-id> \
  --report-relative-path reports/<name>.json \
  --expected-duration-seconds <seconds>
```

## 5. 개인정보 경계

- 오디오 파일은 외장 storage `raw/audio/<candidate-id>/`에 mode 0600
- stdout에는 source ID, 크기, codec, SHA-256만 나간다. URI는 나오지 않는다
- 실패 메시지의 URI는 `<private-source-uri>`로 치환된다

## 6. 파일럿 결과 (2026-08-09, source-003)

| 항목 | 값 |
| --- | --- |
| container / codec | `webm` / `opus` |
| format id | 251 |
| sample rate | 48,000 Hz |
| bitrate | 122.2 kbps |
| 크기 | 24.75 MiB (25,947,344 bytes) |
| 소요 시간 | 약 4초 |

28분 18초 오디오가 24.75 MiB였다. 나머지 2개 source를 같은 조건으로 받으면
총 90 MiB 미만으로 추정한다. 저장 공간과 시간은 제약이 아니다.

## 7. 알려진 한계

- 실제 재생 길이를 검증하지 않았다. ffprobe가 없어 컨테이너를 열어볼 수 없고,
  크기 하한 검사로만 잘린 다운로드를 거른다
- opus 48 kHz는 STT와 voice 기준선 모두에 충분하지만, 원본 마스터가 아니라
  서비스가 재인코딩한 스트림이다. 원본 음질의 상한이 아니다
- 영상은 받지 않았다. 행동·분위기 annotation이 영상을 요구하면 그때 별도로 정한다
- 화자 분리는 하지 않았다. 이 파일에는 Reference 외의 화자도 들어 있다
