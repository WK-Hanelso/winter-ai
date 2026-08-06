# winter-ai

로컬 중심 Personal Companion을 만드는 프로젝트입니다. 같은 사용자의 대화,
기억, 선호와 관계 맥락을 장기간 유지하면서 CLI와 Voice에서 하나의 정체성으로
동작하는 것을 목표로 합니다.

## 현재 상태

Milestone 0의 기반 구조와 Milestone 1의 첫 CLI 경로가 준비되어 있습니다. Docker
개발 이미지, Python 패키지, Port 계약, deterministic fake adapter, 공유
`CompanionCore`, CLI/Voice orchestration 테스트를 갖췄습니다. 실제 local
llama.cpp server를 선택하면 CLI가 `CompanionCore`를 거쳐 Qwen3 응답을 받습니다.
대화 SQLite 영속화와 실제 Voice adapter는 아직 구현하지 않았습니다.

첫 Local LLM probe도 성공했습니다. Docker 안의 llama.cpp Vulkan runtime으로
Qwen3-4B-Instruct-2507 Q4_K_M을 RTX 2060 6 GiB에서 실행했고, 37/37 레이어가
GPU에 올라간 상태로 한국어 응답을 생성했습니다. 정확한 모델 출처·해시·성능은
[모델 선정 문서](docs/model-selection.md)에 기록합니다.

한국어 STT도 Whisper small과 공개 Zeroth-Korean fixture로 local CPU 전사에
성공했습니다. 현재 NVIDIA driver와 공식 CUDA image의 요구 버전이 맞지 않아 STT
CUDA 경로는 명시적으로 실패하며, 이 제한과 CPU 결과를 같은 문서에 기록합니다.

한국어 TTS는 MeloTTS로 local WAV 합성에 성공했습니다. 이는 독립 runtime probe이며
최종 Companion Voice 또는 특정 인물 음성 복제를 의미하지 않습니다.

CPU 개발 환경을 기본값으로 두고, GPU와 Voice 장치는 명시적인 Compose overlay에서만
전달합니다. 선택 근거는 [ADR-0001](docs/adr/0001-docker-development-baseline.md)에
있습니다.

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

`dev` 서비스는 Docker `local` logging driver를 사용하며, 로그는 10MB 파일 3개로
제한된다. 장시간 model probe가 Host 디스크를 과도하게 점유하지 않게 하기 위한 설정이다.

GPU 장치가 필요한 후속 adapter 작업에서만 GPU overlay를 명시합니다.

```bash
docker compose -f compose.yaml -f compose.gpu.yaml run --rm dev bash
```

Voice 작업에서는 먼저 `.env.example`을 `.env`로 복사하고 Host의 실제
PulseAudio socket 경로를 확인한 뒤 Voice overlay를 명시합니다.

```bash
docker compose -f compose.yaml -f compose.voice.yaml run --rm dev bash
```

기본 CLI는 의도적으로 fake adapter를 사용합니다. 이는 오프라인 개발과 테스트를
모델 서버의 상태에서 분리하기 위한 명시적 선택이며, `--backend local`이 실패해도
fake 응답으로 자동 전환하지 않습니다.

```bash
docker compose run --rm dev python -m companion.cli --backend fake --prompt "안녕"
```

실제 local LLM CLI는 Host의 runtime과 모델을 **read-only** mount한 `llm` 서비스와,
그 서비스에만 접속하는 `dev` CLI 컨테이너로 구성됩니다. 아래 변수 경로는 Host
경로이고, 컨테이너에서는 각각 `/runtime`, `/models`로만 보입니다.

```bash
export LLAMA_RUNTIME_DIR=/path/to/llama-b10276-parent
export LLM_MODEL_DIR=/path/to/gguf-directory
export LLM_MODEL_FILE=Qwen3-4B-Instruct-2507.Q4_K_M.gguf

docker compose -f compose.yaml -f compose.llm.yaml -f compose.gpu.yaml up -d llm
docker compose -f compose.yaml -f compose.llm.yaml -f compose.gpu.yaml run --rm dev \
  python -m companion.cli --backend local --prompt "한국어로 한 문장만 인사해줘"
docker compose -f compose.yaml -f compose.llm.yaml -f compose.gpu.yaml down
```

`LLAMA_RUNTIME_DIR`에는 그 아래에 `llama-b10276/llama-server`가 있는 디렉터리를
지정합니다. `llm`은 Host port를 공개하지 않으며 Compose 내부 `http://llm:8080`에서만
통신합니다.

대화를 프로그램 재시작 뒤에도 보존하려면 명시적으로 SQLite 경로를 지정합니다.
Docker 실행에서는 컨테이너 내부 `/tmp`가 실행마다 사라지므로, Host의 `./data`와
연결된 `/workspace/data` 아래를 사용합니다. `data/`와 `*.sqlite`는 Git에서 제외됩니다.
Host에서 DB를 직접 수정·삭제할 수 있도록, 처음 한 번 현재 사용자 UID/GID를 넘깁니다.

```bash
export LOCAL_UID=$(id -u)
export LOCAL_GID=$(id -g)

docker compose run --rm dev python -m companion.cli \
  --backend fake \
  --conversation-db /workspace/data/conversations.sqlite \
  --prompt "이 대화를 저장해줘"

docker compose run --rm dev python -m companion.cli \
  --backend fake \
  --conversation-db /workspace/data/conversations.sqlite \
  --show-history
```

현재 저장하는 것은 순서가 있는 원문 대화 기록뿐입니다. 이전 turn을 LLM prompt에
자동 주입하는 context builder와 장기 기억 lifecycle은 아직 추가하지 않았습니다.

테스트는 다음 명령으로 실행합니다.

```bash
docker compose run --rm dev pytest
```

실제 LLM checkpoint는 Git과 Docker 이미지에 넣지 않습니다. 내려받은 파일의 경로를
명시해 Host에서 다음 수동 probe를 실행할 수 있습니다.

```bash
python3 experiments/local_llm_probe.py \
  --runtime-dir /path/to/llama-b10276 \
  --model-path /path/to/Qwen3-4B-Instruct-2507.Q4_K_M.gguf
```

이 probe는 Docker GPU를 사용하며, 파일을 read-only로 mount합니다. 모델 파일의
정확한 SHA-256과 검증 결과는 [모델 선정 문서](docs/model-selection.md)를 따릅니다.

한국어 STT CPU probe는 다음처럼 실행합니다.

```bash
python3 experiments/stt_probe.py \
  --model-path /path/to/ggml-small.bin \
  --audio-path /path/to/korean-fixture.flac
```

한국어 TTS probe는 별도 pinned Docker image를 build한 뒤 실행합니다.

```bash
docker build -f Dockerfile.melotts-probe -t winter-ai:melotts-probe .
python3 experiments/tts_probe.py \
  --cache-dir /path/outside/git/melotts-cache \
  --output-path /path/outside/git/melotts-korean.wav
```

모델 선택값은 `configs/models/`의 Python profile로 관리합니다. 기본값은 `base`,
RTX 2060 6 GiB profile은 `rtx2060_6gb`(Vulkan GPU backend, 37 layers), CPU
profile은 `cpu`입니다.
