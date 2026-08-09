# Reference Source Metadata Preflight

승인된 Human Reference source의 **메타데이터만** 조회하는 probe의 실행 규약과
실패 정책이다. 이 probe는 media·subtitle·thumbnail을 다운로드하지 않는다.

관련: [reference-data-storage.md](reference-data-storage.md),
[reference-human-selection.md](reference-human-selection.md),
[roadmap.md](roadmap.md) Order 5.

---

## 1. 이 probe가 답하는 질문

실제 원본을 외장 storage에 받기 전에 다음을 확인한다.

- 승인된 source가 지금도 접근 가능한가 (지역·연령·비공개·삭제·live 여부)
- 전체 raw duration이 3시간 cap 안에 들어오는가
- 사용 가능한 audio format과 sample rate가 존재하는가
- 한국어 자막 또는 automatic caption이 있는가

답이 부정적이면 다음 단계(실제 수집)로 넘어가지 않는다.

## 2. 구성 요소

| 파일 | 역할 |
| --- | --- |
| `src/companion/reference_source_probe.py` | domain 로직. yt-dlp를 `SourceMetadataRunner` protocol 뒤에 둔다 |
| `experiments/reference_source_probe.py` | CLI 진입점 |
| `Dockerfile.reference-source-probe` | pinned probe runtime |
| `compose.reference-source.yaml` | read-only 실행 정의 |
| `tests/unit/test_reference_source_probe.py` | fake runner 기반 오프라인 테스트 |

Domain 계층은 yt-dlp를 직접 import하지 않는다. `YtDlpMetadataRunner`가
subprocess adapter이며, 테스트는 protocol을 만족하는 fake로 대체한다
(AGENTS.md의 adapter 경계 규칙).

## 3. 실행

외장 storage가 mount돼 있고 `.env`에 `REFERENCE_STORAGE_ROOT`와
`REFERENCE_STORAGE_ID`가 설정돼 있어야 한다.

```bash
docker compose -f compose.reference-source.yaml run --rm reference-source-probe \
  --candidate-id <candidate-id> \
  --report-relative-path reports/<name>.json
```

`--candidate-id`와 report 이름은 외장 private manifest를 보고 사용자가 정한다.
Git·Issue·PR·터미널 로그에 실제 URL이나 identity를 쓰지 않는다.

기본 테스트는 인터넷·GPU·외장 drive 없이 통과한다.

```bash
docker compose run --rm dev pytest
```

## 4. 격리 규약

`compose.reference-source.yaml`이 강제하는 것.

- `read_only: true` — 컨테이너 루트 파일시스템 쓰기 금지
- 저장소는 `:ro`로 mount. probe가 작업 트리를 수정할 수 없다
- 쓰기 가능한 경로는 tmpfs `/tmp`와 외장 storage mount뿐
- `YTDLP_NO_PLUGINS=1` — 외부 plugin 로드 금지
- 로그 rotation 10 MiB × 3 — 장시간 실행이 Host 디스크를 잠식하지 않게 한다

yt-dlp 호출은 `--simulate --dump-single-json`으로 고정하며
`--ignore-config --no-cache-dir --no-playlist`를 함께 준다. Host의 yt-dlp
설정 파일이 실행에 개입하지 못하게 하기 위해서다.

cookie, browser profile, proxy, 인증 우회는 사용하지 않는다.

## 5. 실패 정책

**silent fallback을 하지 않는다.** 모든 실패는 명시적 오류다.

| 상황 | 동작 |
| --- | --- |
| source 하나 조회 실패 | 해당 source를 `failures`에 기록하고 나머지는 계속. exit 2 |
| yt-dlp가 malformed JSON 반환 | `ReferenceSourceProbeError`. 추측 복구 없음 |
| audio format이 하나도 없음 | 해당 source 실패 처리 |
| duration이 없거나 0 이하 | 해당 source 실패 처리 |
| runner가 다른 source ID/URI 반환 | 전체 중단. 보고 신뢰성 문제이므로 부분 성공으로 처리하지 않는다 |
| 임시 디렉터리에 파일이 생성됨 | 즉시 실패. "다운로드하지 않는다"는 계약의 런타임 검증 |
| 총 duration이 cap 초과 | 보고는 하되 exit 3. 다음 단계 진행 거부 |
| candidate 미등록 / source 0건 | 즉시 실패 |
| report 경로가 이미 존재 | `O_EXCL`로 실패. 기존 보고를 덮어쓰지 않는다 |

exit code: `0` 정상, `2` 실패 존재, `3` cap 초과.

## 6. 개인정보 경계

두 개의 출력이 있고 담는 내용이 다르다.

- `public_probe_summary()` — 터미널·로그용. source ID, duration, availability,
  audio format 개수, 자막 언어 코드만 담는다
- `private_probe_report()` — 외장 storage 전용. URL·제목·업로더·format 상세를 담는다

private report는 외장 storage의 `reports/*.json`에만 mode 0600으로 쓴다.
경로는 `reports/` 직하위 `.json` 하나로 제한하며, 상위 디렉터리가 symlink이면
거부한다.

실패 메시지의 URL은 `<private-source-uri>`로 치환하고 500자로 자른다.
yt-dlp의 stderr가 그대로 로그에 흘러 URL이 유출되는 것을 막는다.

## 7. 설계 결정 기록

### 7.1 Node base image

`Dockerfile.reference-source-probe`는 Python 프로젝트인데도
`FROM node:22-bookworm-slim`을 쓴다. **실수가 아니다.**

최근 yt-dlp의 YouTube extractor는 player script 해석에 외부 JavaScript
runtime을 요구한다. runtime이 없으면 metadata 조회가 실패하거나 신뢰할 수 없는
결과를 낸다. Host에 Node를 임의 설치하지 않는다는 Docker-first 원칙
([ADR 0001](adr/0001-docker-development-baseline.md))을 지키기 위해, JS runtime과
yt-dlp를 함께 고정한 격리 image로 분리했다.

Node base에 python3를 얹는 쪽이 python base에 Node를 얹는 쪽보다 단순해서
그 방향을 택했다.

> 이 근거가 파일에 없던 동안 리뷰에서 실제로 "Python 프로젝트인데 Node base"라는
> 오판이 발생했다. Dockerfile 상단 주석과 이 절이 그 재발 방지 기록이다.

### 7.2 dependency lock을 두지 않은 이유

`requirements/melotts-probe.lock`은 있는데 이 probe에는 lock이 없다. 의도적이다.

melotts lock은 일반 정책이 아니다. MeloTTS를 git ref에서 `pip install -e`로
설치하는 구조라 transitive dependency가 전부 floating이었고, 실제로 발생한
충돌을 실험으로 풀어 얻은 결과를 기록한 것이다. 파일 첫 줄의
`# Verified CLI-probe compatibility set`이 그 성격을 말한다.

이 probe는 `yt-dlp[default]==2026.7.4`로 PyPI 정확 version을 pin하며 관측된
충돌이 없다. 남은 재현성 갭은 transitive dependency가 pin되지 않는다는 점이다.
갭 자체는 실재하지만 해소하려면 network가 붙은 실제 image build로 resolved set을
추출해야 하고, 이는 "기본 검증은 오프라인"이라는 이 issue의 전제와 분리된다.

따라서 lock 도입은 별도 issue로 분리한다. 이 문서가 그 판단의 근거이며,
lock을 추가할 때 이 절을 갱신한다.

## 8. 알려진 한계

- transitive dependency가 pin되지 않아 image rebuild 시 drift 가능 (§7.2)
- extractor는 원격 서비스 변경에 취약하다. yt-dlp version을 올릴 때마다
  metadata 필드 가정(`duration`, `formats`, `acodec`, `subtitles`)을 재확인해야 한다
- `availability`가 없으면 `unknown`으로 기록한다. 접근 가능성을 단정하지 않는다
- 자막 언어는 코드 목록만 본다. 실제 자막 품질과 싱크는 검증하지 않는다
- 이 probe는 접근 가능성만 답한다. 사용 가능한 subset 비율은 Order 5의
  다음 단계에서 사람이 판단한다
