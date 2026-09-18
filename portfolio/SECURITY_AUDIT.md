# Portfolio Confidentiality Audit

- Audit date: 2026-09-19
- Scope: `portfolio/`의 Markdown, JSON, SVG, Python 생성 스크립트와 PNG embedded strings
- Result: **PASS**

## 검사 항목

- 내부 absolute path와 mount path
- IPv4, mesh-VPN 주소, hostname, private API endpoint, port 표기
- topic name, vehicle identifier와 parameter
- 인증정보와 비밀값 할당 패턴
- 프로젝트에서 알려진 개인 식별 문자열, private candidate/source identifier
- audio/video filename과 media extension
- 원문 대화·자막·오디오 payload

## 실행한 검사

```bash
rg -n -P '(?<![A-Za-z0-9])/(home|mnt|media|srv|opt|var|Users)/|\b(?:[0-9]{1,3}\.){3}[0-9]{1,3}\b|private endpoint/credential patterns|source-[0-9]+|candidate-[0-9]+|media filename patterns' \
  portfolio --glob '*.md' --glob '*.json' --glob '*.svg' --glob '*.py'

rg -n '<project-known personal markers>' \
  portfolio --glob '*.md' --glob '*.json' --glob '*.svg' --glob '*.py'

strings portfolio/assets/*.png | rg -n -P \
  'absolute path|IP|endpoint/credential|private identifier|media filename patterns'

python3 -m json.tool portfolio/evidence.json >/dev/null
file portfolio/assets/*.png portfolio/assets/*.svg
```

실제 실행에서는 위 축약 표기의 각 범주를 명시적 정규식으로 확장했다. SVG 표준 namespace(`w3.org`, `purl.org`, `creativecommons.org`)는 format metadata이므로 private endpoint 검사에서 allowlist했다.

## 결과와 조치

- Final scan: 공개 금지 항목 발견 없음.
- 초기 PNG metadata에서 plotting library의 공개 website 문자열이 발견됐다. private endpoint는 아니지만 불필요한 metadata이므로 생성 스크립트에서 `Software` metadata를 제거하고 전 자산을 다시 생성했다.
- 실제 SHA-256, byte size, codec 값, source URI, media filename은 넣지 않고 field schema만 시각화했다.
- 원시 평가 record는 공개 저장소에서 `NOT_AVAILABLE`로 기록했으며 private storage를 읽거나 복사하지 않았다.
- PNG 폭은 2,400px 이상이며 SVG 폭은 1,152pt(16in, CSS 기준 1,536px) 이상이다.
