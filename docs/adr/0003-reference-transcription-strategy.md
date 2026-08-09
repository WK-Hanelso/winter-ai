# ADR-0003: Reference 전사 파이프라인 전략과 측정 시행착오

- Status: Accepted
- Date: 2026-08-09
- Issues: [#75](https://github.com/WK-Hanelso/winter-ai/issues/75),
  [#77](https://github.com/WK-Hanelso/winter-ai/issues/77),
  [#82](https://github.com/WK-Hanelso/winter-ai/issues/82),
  [#84](https://github.com/WK-Hanelso/winter-ai/issues/84),
  [#86](https://github.com/WK-Hanelso/winter-ai/issues/86),
  [#88](https://github.com/WK-Hanelso/winter-ai/issues/88)

## Context

M4 Order 5는 3시간 이하 raw source에서 usable subset을 만드는 단계다. 이 ADR은 그
과정에서 내린 전략적 결정과, **틀렸다가 고친 것들**을 함께 기록한다.

후자가 더 중요하다. 결론만 남기면 다음 세션이 같은 함정을 다시 밟는다.

## Decision

### D1. 추측하지 않고 측정한다

각 단계에서 "아마 이럴 것"으로 진행하지 않고 측정 도구를 먼저 만든다. 도구가 없으면
도구를 만드는 것이 그 단계의 작업이 된다.

근거: source 하나를 잘못 판단하면 1시간 41분 분량의 전사·검수를 되돌려야 한다.
측정 비용은 그보다 훨씬 싸다.

### D2. 파일럿을 먼저 한다

전체를 처리하기 전에 가장 작은 단위로 전 과정을 통과시킨다.

- 수집: 가장 짧은 source 하나(28분)만 먼저
- 전사: 5분 구간만 먼저

실패해도 손실이 그 단위로 제한된다.

### D3. 자막은 정답이 아니라 대조 자료다

`docs/human-reference-design.md`가 요구하는 `raw_transcript`는 "반복, 머뭇거림과
비문을 보존"해야 한다. 사람이 만든 자막은 그 반대 방향으로 편집된다. 측정 결과
필러의 약 절반이 사라져 있었다.

따라서 사람 자막은 `normalized_transcript`와 정렬 참고자료로 쓰고, `raw_transcript`는
STT로 만든다.

### D4. Voice Set을 교체가 아니라 추가로 확보한다

STT 품질 저하의 원인이 모델인지 화자 겹침인지 구분하려면 단독 화자 source가 필요했다.
이때 기존 대화형 source를 교체하는 선택지가 있었으나 **추가**를 택했다.

근거: `docs/reference-human-selection.md`의 hard gate가 양쪽을 모두 요구한다.
H5는 단독 화자 구간을, H3·H4·H6은 자연스러운 대화와 둘 이상의 interlocutor를
요구한다. 교체하면 후보 자체가 reject된다. `docs/human-reference-design.md`가
정의한 `Behavior Set` / `Voice Set` 구분이 이미 이 상황을 예상하고 있었다.

즉 기존 구성은 Behavior Set만 있고 Voice Set이 0개인 미충족 상태였고, 이 작업은
그 결손을 메운다.

### D5. 개인정보 경계를 코드로 강제한다

- 상세 정보는 외장 storage에만, mode 0600으로 쓴다
- stdout에는 `public_*_summary()`가 만든 sanitize된 지표만 나간다
- 실패 메시지의 URI는 `<private-source-uri>`로 치환한다
- 테스트 fixture는 전부 합성 문장이며 실제 자막·전사를 포함하지 않는다
- 미디어·자막·전사 확장자를 `.gitignore`에 넣는다

주석이나 규칙이 아니라 검사와 테스트로 강제한다.

## 시행착오 기록

### E1. 자기 자신과 비교해 "완벽한 자막" 1.0이 나왔다

**증상**: `source-001`의 사람 자막 대 자동 자막 유사도가 1.0으로 나왔다. 읽으면
"자막이 원본에 완벽히 충실하다"는 뜻이다.

**원인**: 그 source에는 사람 자막이 없다. yt-dlp는 `--sub-langs ko,ko-orig`를 받으면
사람 자막이 없을 때 자동 caption을 **양쪽 이름으로 저장한다.** md5가 일치했다.
같은 파일을 자기 자신과 비교한 것이다.

**교훈**: 완벽한 점수는 신뢰의 근거가 아니라 의심의 근거다. 지금은
`identical_tracks=True`로 표시하고 비교값을 내지 않는다.

### E2. rolling caption 중복 제거가 부실해 기준값이 37% 부풀려졌다

**증상**: whisper가 service caption 대비 문자 수 52%만 산출했다. "whisper small은
`raw_transcript`를 만들지 못한다"고 결론냈다.

**원인**: YouTube 자동 caption은 직전 cue의 **꼬리**를 다음 cue의 **머리**에 반복한다.
파서는 "직전 cue 전체가 접두사인 경우"만 걸렀고 부분 겹침은 통과시켰다.
실측: 원시 8,599자 → 기존 파서 5,669자 → 토큰 단위 제거 3,547자. **37% 과대계상.**

**결과**: 이 하나의 버그가 두 issue의 결론을 뒤집었다.

| 지표 | 버그 상태 | 수정 후 |
| --- | ---: | ---: |
| 사람 자막 ÷ 자동 자막 문자비 (source-002) | 0.722 | **1.416** |
| 사람 자막 ÷ 자동 자막 문자비 (source-003) | 0.737 | **1.349** |
| 사람 자막 필러 잔존 (source-002) | 0.271 | **0.532** |
| 사람 자막 필러 잔존 (source-003) | 0.245 | **0.476** |
| whisper ÷ service 문자비 (source-003 10:00) | 0.529 | **1.025** |
| whisper ÷ service 문자비 (source-004 10:00) | 0.627 | **0.993** |
| whisper ↔ service 일치도 (source-003 10:00) | 0.498 | **0.848** |

**정정된 결론**:

- 사람 자막이 문자를 26~28% **삭제**한다 → 틀렸다. 오히려 35~42% **더 많다**
- 필러를 75% 삭제한다 → 약 50% 삭제다. 방향은 맞고 크기는 과장이었다
- whisper small이 내용의 절반을 빠뜨린다 → **틀렸다.** 문자 수는 service ASR과
  거의 같고(0.96~1.03) 두 시스템의 일치도는 0.74~0.85다

**교훈**: 측정 도구 자체를 검증하지 않으면 측정 결과가 결론을 만든다.
정규화 로직은 원시값·현재값·더 엄격한 값을 나란히 비교해 검증해야 한다.

### E3. 문장부호 유무를 source 간 신호로 쓸 뻔했다

**증상**: `source-003`의 자동 caption은 문장부호가 0인데 `source-004`는 1000자당
63개였다. 트랙을 잘못 받은 것으로 의심했다.

**확인**: `ko-orig`만 단독으로 다시 받아 md5를 비교했더니 일치했다. 오염이 아니었다.

**원인**: 자막 생성 시점이 다르다. 최신 YouTube ASR은 문장부호를 붙인다.

**교훈**: "문장부호가 있으면 사람이 다듬은 것"은 **같은 source 안의 두 트랙을 비교할
때만** 성립한다. source끼리 비교하는 신호로 쓰면 안 된다.

### E4. 컨테이너가 결과물을 root 소유로 만들었다

`docker run`에 `--user`를 주지 않아 whisper가 만든 transcript를 Host가 chmod조차
하지 못했다. 첫 실행이 그 지점에서 실패했다. 중간 WAV도 기본 umask로 644가 남았다.

**교훈**: private 데이터를 만드는 컨테이너는 반드시 Host user로 실행하고, 산출물의
권한을 명시적으로 되돌린다.

### E5. 구간마다 원본을 복제했다

전사 작업 디렉터리에 원본을 구간 단위 이름으로 staging해서, 구간을 늘릴 때마다
25 MiB 원본이 복제됐다. 외장 storage 사용량이 94 MiB까지 늘었다가 source 단위
staging으로 바꿔 45 MiB가 됐다.

### E6. 걱정의 방향이 틀렸다

`source-001`에 사람이 만든 한국어 자막이 없어 "이 source만 전사 비용이 크다"고
보고했다. 측정 결과 사람 자막은 어느 source에서도 `raw_transcript`로 쓸 수 없어,
세 source의 전사 비용은 같았다. 우려 자체가 근거 없이 사라졌다.

**교훈**: 비용 차이를 주장하기 전에 그 자원이 실제로 쓸 수 있는 것인지 먼저 확인한다.

### E7. 저장소에 자막·전사 확장자가 무방비였다

`.gitignore`에 미디어 확장자는 있었으나 `.vtt`, `.srt`, `.opus`, `.ogg`가 없었다.
자막과 전사는 텍스트라 오히려 검색되기 쉬운 개인정보다. 실제 유입은 없었고 지금은
막았다.

## Consequences

- 각 probe는 실행뿐 아니라 **자기 측정의 타당성**을 함께 문서화한다
- 지표 계산 로직은 한 곳에만 둔다. `reference_subtitle_probe`의 함수를 공개 API로
  올려 전사 비교에서 재사용한다. 두 곳에 두면 정의가 어긋난다
- 결론을 뒤집는 수정이 발생하면 이전 보고를 정정한다. #75에 정정 보고를 남겼다
- 파일럿 단위가 작아 세 번의 결론 번복이 각각 몇 분~몇십 분의 재작업으로 끝났다

## 현재 확정된 것

- 사람 자막: `raw_transcript` 불가. `normalized_transcript`와 정렬 참고자료로 사용
- 자동 caption과 whisper small: 문자량이 거의 같고 일치도 0.74~0.85. 둘 다 후보
- 수집·전사 비용은 병목이 아니다. 28분 오디오 24 MiB / 4초, 전사는 실시간 대비 3~4.6배
- 남은 병목은 **화자 분리**다. 대화형 source에서 Reference의 발화만 분리해야 한다

## 미해결

- 두 ASR 중 어느 쪽이 정확한지는 정답이 없어 판정 불가. 일치·불일치만 측정 가능
- whisper medium 이상은 비교하지 않았다
- 대화형 source에서 whisper의 시간 커버리지가 낮다(0.596 대 0.787). 단독 화자
  source에서는 차이가 사라진다. 화자 겹침이 원인이라는 정황이나 확정은 아니다
- diarization 방식은 아직 선택하지 않았다
