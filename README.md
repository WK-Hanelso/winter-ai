# winter-ai

로컬 중심 Personal Companion을 만드는 프로젝트입니다. 같은 사용자의 대화,
기억, 선호와 관계 맥락을 장기간 유지하면서 CLI와 Voice에서 하나의 정체성으로
동작하는 것을 목표로 합니다.

## 현재 상태

프로젝트는 초기 환경 기준을 확정하는 단계입니다. 아직 Dockerfile, Python
애플리케이션, 모델 런타임, 모델 checkpoint는 추가하지 않았습니다.

Host의 Docker Engine, NVIDIA GPU runtime, PulseAudio와 기본 녹음·재생 장치는
검증되었습니다. 실제 GPU 컨테이너 실행과 모델 연결은 아직 수행하지 않았습니다.

첫 개발 컨테이너는 Python 3.11과 `python:3.11-slim-bookworm`을 기준으로
구성할 예정입니다. CPU 개발 환경을 기본값으로 두고, GPU와 Voice 장치는
명시적인 Compose overlay에서만 전달합니다. 선택 근거는
[ADR-0001](docs/adr/0001-docker-development-baseline.md)에 있습니다.

현재 진행 중인 작업은 [#1 Host/Container baseline 확정](https://github.com/WK-Hanelso/winter-ai/issues/1)입니다.

## 개발 원칙

- Docker-first: 애플리케이션과 의존성은 컨테이너에서 실행합니다.
- Local-first: 초기 대화 기능은 외부 상용 LLM API에 의존하지 않습니다.
- Shared Core: CLI와 Voice는 하나의 `CompanionCore`를 공유합니다.
- Offline tests: 기본 테스트는 인터넷, GPU, 마이크, 모델 가중치, API 키 없이 실행됩니다.
- Privacy: 음성, 대화 DB, 모델 파일과 비밀 정보는 Git에 저장하지 않습니다.

Host와 Container의 책임, 조사된 환경 사양, 재확인해야 할 항목은
[환경 기준 문서](docs/environment.md)에 기록합니다.

## 작업 방식

모든 작업은 GitHub Issue로 시작하며, 기능·버그·조사·설계 결정을 동일한
상태 전이 형식으로 기록합니다. 원격 저장소에 push하기 전에는 README가 변경된
프로젝트 상태와 사용 방법을 정확히 반영하는지 확인합니다.

세부 제품·아키텍처·개발 규칙은 [AGENTS.md](AGENTS.md)를 기준으로 합니다.

## 개발 환경 스펙

| 구분 | 기준 |
| --- | --- |
| Container Python | 3.11 |
| 기본 이미지 | `python:3.11-slim-bookworm` |
| 기본 실행 | CPU-only Docker Compose 서비스 `dev` |
| GPU | RTX 2060 6 GiB를 Host에서 확인; `compose.gpu.yaml`을 명시할 때만 전달 |
| Voice | PulseAudio/ALSA를 Host에서 확인; `compose.voice.yaml`과 Pulse socket을 명시할 때만 전달 |
| 모델·개인 데이터 | 이미지와 Git에서 제외, `data/`·`models/` bind mount로만 전달 |

## Docker 실행

기본 CPU 개발 셸을 build하고 Python runtime을 확인합니다.

```bash
docker compose build
docker compose run --rm dev python --version
```

GPU 장치가 필요한 후속 adapter 작업에서만 GPU overlay를 명시합니다.

```bash
docker compose -f compose.yaml -f compose.gpu.yaml run --rm dev bash
```

Voice 작업에서는 먼저 `.env.example`을 `.env`로 복사하고 Host의 실제
PulseAudio socket 경로를 확인한 뒤 Voice overlay를 명시합니다.

```bash
docker compose -f compose.yaml -f compose.voice.yaml run --rm dev bash
```

이 환경은 아직 모델, Python 패키지, CLI, Voice 애플리케이션을 포함하지 않습니다.

현재는 실제 모델 대신 runtime-독립적인 Port 계약과 구조화된 응답 타입만 정의되어
있습니다. 테스트는 다음 명령으로 실행합니다.

```bash
docker compose run --rm dev pytest
```

첫 local runtime 후보와 probe 순서는 [모델 선정 문서](docs/model-selection.md)에
기록합니다. 실제 모델 checkpoint는 아직 내려받지 않았습니다.
