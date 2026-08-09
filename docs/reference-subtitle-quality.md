# Reference 자막 품질 측정

승인된 source의 한국어 자막이 `raw_transcript`로 쓸 수 있는 품질인지 **측정**하는
절차와 판정 기준이다. media는 다운로드하지 않고 자막 텍스트만 다룬다.

관련: [human-reference-design.md](human-reference-design.md),
[reference-scene-schema.md](reference-scene-schema.md),
[reference-source-probe.md](reference-source-probe.md), [roadmap.md](roadmap.md) Order 5.

---

## 1. 이 측정이 필요한 이유

[human-reference-design.md](human-reference-design.md)와
[reference-scene-schema.md](reference-scene-schema.md)는 `raw_transcript`가
**"반복, 머뭇거림과 비문을 보존"**해야 한다고 규정한다.

사람이 만든 자막은 보통 그 반대 방향으로 편집된다. 읽기 쉽도록 필러를 지우고
반복을 줄이고 문장으로 다듬는다. 자막 제작 관점의 고품질이 이 프로젝트가 필요로
하는 것과 정반대일 수 있다는 뜻이다.

잘 다듬어진 자막은 `raw_transcript`가 아니라 `normalized_transcript`에 해당한다.
스키마상 다른 필드이며 원본을 덮어써서는 안 된다.

**따라서 "자막이 있다"는 사실만으로 전사 비용을 판단할 수 없다.** 편집 강도를
측정해야 한다.

## 2. 측정 방법

핵심 착상은 **한 source 안에서 두 트랙을 비교**하는 것이다.

| 트랙 | 성격 |
| --- | --- |
| `ko` | 사람이 올린 자막. 편집돼 있을 것으로 예상 |
| `ko-orig` | 원본 음성의 자동 생성 caption. 기계 전사에 가까움 |

두 트랙의 차이가 곧 **사람이 가한 편집의 강도**다. 외부 기준 없이 source 자체에서
근거가 나온다는 점이 이 방법의 장점이다.

### 지표

| 지표 | 의미 | 편집이 심하면 |
| --- | --- | --- |
| `filler_retention_ratio` | 사람 트랙 필러 수 ÷ 자동 트랙 필러 수 | 0에 가까움 |
| `character_ratio` | 사람 트랙 문자 수 ÷ 자동 트랙 문자 수 | 1보다 작음 |
| `similarity_ratio` | 두 트랙 텍스트의 `SequenceMatcher` 비율 | 낮음 |
| `punctuation_per_1000_characters` | 문장부호 밀도 | 사람 트랙만 높음 |
| `coverage_ratio` | 자막 시간 합 ÷ 영상 길이 | — (결손 구간 파악용) |
| `mean_cue_characters` / `mean_cue_seconds` | cue 밀도 | 사람 트랙이 성기고 김 |

`filler_retention_ratio`가 가장 직접적인 판정 근거다. 머뭇거림 보존이
`raw_transcript`의 정의 그 자체이기 때문이다.

### 판정 기준

- `filler_retention_ratio`가 0.5 미만이면 사람 트랙은 `raw_transcript`로 쓸 수 없다.
  절반 이상의 머뭇거림이 사라졌다는 뜻이다.
- 이 경우 사람 트랙은 `normalized_transcript` 또는 정렬 참고자료로만 쓴다.

## 3. 구현상 주의점

### 3.1 rolling caption 중복 제거

YouTube 자동 caption은 직전 줄을 반복하고 새 단어를 덧붙이는 방식으로 나온다.
그대로 세면 모든 문자 지표가 몇 배로 부풀려진다. `parse_webvtt()`는 직전 cue를
그대로 확장한 cue에서 **새로 추가된 부분만** 남긴다.

### 3.2 필러는 단어 단위로만 센다

`그것`의 `그`는 머뭇거림이 아니다. `_filler_hits()`는 한글 단어 경계로 잘라
완전히 일치하는 것만 센다. 부분 문자열로 세면 일반 어휘가 필러로 잡혀 측정이
무의미해진다.

### 3.3 동일 트랙 판정 — 가장 중요한 함정

**source에 사람 자막이 없어도 파일은 두 개 생긴다.** yt-dlp가 자동 caption을
`ko`와 `ko-orig` 양쪽 이름으로 저장하기 때문이다.

이를 그대로 비교하면 같은 파일을 자기 자신과 비교하게 되어 `similarity_ratio`가
**1.0**으로 나온다. 이 숫자는 "자막이 원본에 완벽히 충실하다"로 읽히지만 실제
의미는 정반대다. **측정할 사람 자막이 아예 없다는 뜻이다.**

실제로 이 프로젝트의 첫 측정에서 발생했다. 지금은 두 트랙의 정규화 텍스트가
같으면 `identical_tracks=True`로 표시하고 비교값을 내지 않는다.

## 4. 실행

```bash
docker compose -f compose.reference-source.yaml run --rm reference-subtitle-probe \
  --candidate-id <candidate-id> \
  --report-relative-path reports/<name>.json \
  --duration <source-id>=<seconds>
```

`--duration`은 [#77 probe](reference-source-probe.md)에서 얻은 값을 넣는다.
커버리지 계산에만 쓰이며 없으면 `coverage_ratio`가 생략된다.

기본 테스트는 인터넷·실제 source·외장 drive 없이 통과한다.

```bash
docker compose run --rm dev pytest
```

## 5. 격리와 개인정보 경계

[reference-source-probe.md](reference-source-probe.md) §4, §6과 같은 규약을 따른다.
차이점만 적는다.

- 이 probe는 **파일을 만든다.** metadata probe와 달리 "파일이 생기면 실패"가
  아니다. 대신 `.vtt` / `.srt` 외의 파일이 생기면 실패 처리한다
- 자막 파일은 외장 storage `raw/subtitles/<candidate-id>/`에 mode 0600으로 저장
- **자막 원문은 저장소·stdout·test fixture에 넣지 않는다.** 테스트 fixture는 이
  테스트를 위해 작성한 합성 한국어 문장이다
- stdout에는 수치 지표만 나간다. `public_subtitle_summary()`는 텍스트를 담지 않는다

## 6. 측정 결과 (2026-08-09, candidate-001)

| source | 두 트랙 동일 | 필러 잔존 | 문자비 | 유사도 |
| --- | --- | ---: | ---: | ---: |
| source-001 | **예** | — | — | — |
| source-002 | 아니오 | 0.271 | 0.722 | 0.403 |
| source-003 | 아니오 | 0.245 | 0.737 | 0.442 |

문장부호 밀도(1000자당): 사람 트랙 47.9 / 23.6, 자동 트랙 둘 다 **0.0**.

### 판정

사람이 만든 한국어 자막은 **머뭇거림의 약 75%를 삭제**했고 전체 문자의 26~28%가
사라졌다. 문장부호가 붙어 있다는 것도 written form으로 다듬였다는 신호다.

**즉 사람 자막은 `raw_transcript`가 될 수 없다.** `normalized_transcript`와 정렬
참고자료로 쓴다.

### 따라오는 결론

`source-001`에 사람 자막이 없다는 사실은 **추가 비용을 만들지 않는다.** 사람 자막이
있는 source-002, source-003도 어차피 `raw_transcript`를 위해 STT가 필요하기
때문이다. 세 source의 전사 비용은 같다.

이 결론이 이 측정의 목적이었다.

## 7. 알려진 한계

- `similarity_ratio`는 순서와 분절에 민감하다. 편집 강도의 절대 척도가 아니라
  `filler_retention_ratio`를 보조하는 참고값으로만 쓴다
- 필러 목록은 고정 목록이며 개인 말버릇을 반영하지 않는다
- 자동 caption 자체의 인식 오류율은 측정하지 않았다. 음성 없이는 확인할 수 없다
- **두 트랙 모두 화자를 구분하지 않는다.** 다인 발화 source에서 Reference의 발화만
  분리하려면 diarization이 필요하며 이 측정의 범위 밖이다
- 커버리지는 자막이 붙은 시간 비율일 뿐 발화 시간 비율이 아니다
