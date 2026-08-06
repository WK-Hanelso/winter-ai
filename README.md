# winter-ai

로컬 중심 Personal Companion을 만드는 프로젝트입니다. 같은 사용자의 대화,
기억, 선호와 관계 맥락을 장기간 유지하면서 CLI와 Voice에서 하나의 정체성으로
동작하는 것을 목표로 합니다.

## 현재 상태

Milestone 0의 기반 구조, Milestone 1의 CLI 경로, Milestone 2의 Identity·명시적
Memory lifecycle이 준비되어 있습니다. Docker 개발 이미지, Python 패키지, Port 계약,
deterministic fake adapter, 공유 `CompanionCore`, CLI/Voice orchestration 테스트를
갖췄습니다. 실제 local llama.cpp server를 선택하면 CLI가 `CompanionCore`를 거쳐
Qwen3 응답을 받습니다. 대화는 SQLite에 영속화되며, Local CLI는 제한된 최근 대화
context를 다음 요청에 포함합니다. 실제 Voice adapter는 아직 구현하지 않았습니다.

Memory는 대화의 명시적 `기억해` 요청에서만 후보로 생성되고, 사용자 검토 뒤에만
활성화됩니다. 수정 이력, 논리적 폐기, 명시적 물리 삭제를 지원합니다. M2의 수직 단면
검증 결과와 알려진 한계는 [M2 검증 문서](docs/milestone-2-validation.md)에 기록합니다.

Voice Identity 0.1은 Python config의 `neutral`, `calm`, `warm`, `serious` Prosody
profile로 시작합니다. 이는 실제 음색 모델과 분리된 말하기 계획이며, 설계 초안은
[Voice 설계 문서](docs/voice-design.md)에 기록합니다.

## 겨울이 시작하기

먼저 `.env.example`을 `.env`로 복사해 local model 경로를 채운 뒤, 아래 명령으로
시작합니다. 대화·기억·Identity는 모두 Host의 `data/`에 지속 저장됩니다. `start`는
LLM 서버를 시작하고 health 확인을 마친 뒤에만 겨울이 CLI를 엽니다.

```bash
cp .env.example .env
# .env의 LLAMA_RUNTIME_DIR, LLM_MODEL_DIR, LLM_MODEL_FILE을 수정
./winter start
```

개발 중 모델 없이 화면 흐름만 확인하려면 `--backend fake`를 명시합니다.

```bash
docker compose run --rm dev python -m companion.user_cli --backend fake
```

이미 실행 중인 겨울이에 다시 연결하려면 `./winter chat`, 상태 확인은
`./winter status`, 모델 서버를 멈추려면 `./winter stop`을 사용합니다.

대화 중에는 아래 명령을 사용할 수 있습니다. 이 명령들은 대화 기록으로 저장되지
않습니다.

```text
/status    현재 backend·Identity·저장소 상태
/history   저장된 대화 기록
/memories  기억과 lifecycle 상태
/help      명령 목록
/exit      종료
```

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

## Companion Identity

Companion의 이름·역할·핵심 성격·가치관·관계 원칙·변경 불가 경계는 model prompt와
분리된 JSON Identity로 관리합니다. 파일은 개인 설정이므로 `data/` 아래에 두며 Git에
넣지 않습니다. 현재 초기 이름은 **겨울이**이며, 그 밖의 Core Persona는 사용자 승인
없이 자동 변경하지 않습니다. 형식은 [Identity 문서](docs/identity.md)를 따릅니다.

```bash
docker compose run --rm dev python -m companion.cli \
  --identity-path /workspace/data/identity.json --show-identity
```

## Explicit Memory lifecycle

일반 대화는 자동으로 영구 기억이 되지 않습니다. 사용자가 명시적으로 저장한 항목은
처음 `candidate`가 되고, 검토 뒤 `approved`, 그 다음 `active`로 전이합니다.

```bash
docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite \
  --memory-add "천우가 명시적으로 기억해 달라고 한 내용"

docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --memory-approve <memory-id>

docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --memory-activate <memory-id>
```

기억의 내용을 바꿀 때는 기존 행을 덮어쓰지 않습니다. `--memory-replace`는 새
`candidate`를 만들고 기존 기억의 ID를 `supersedes`로 기록합니다. 새 항목이
`active`가 되는 순간에만 기존 active 항목을 `deprecated`로 전환하므로, 검토 중인
수정 때문에 현재 기억을 잃지 않습니다.

```bash
docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite \
  --memory-replace <memory-id> "수정할 내용"

docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --memory-deprecate <memory-id>

docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --list-memories
```

`deprecated`는 이력 보존을 위한 논리적 삭제입니다. 완전히 제거하려면 목록에서 ID를
확인한 뒤 아래처럼 명시적으로 삭제합니다. 다른 Memory가 해당 ID를 `supersedes`로
참조하면 이력 보호를 위해 삭제가 거부됩니다.

```bash
docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --memory-delete <memory-id>
```

자동 conflict 판정은 아직 제공하지 않습니다. 현재는 active Memory 중 현재 질문과
keyword가 겹치는 최대 3개·총 1,000자만 별도 system context로 Local LLM에 전달합니다.
candidate·approved·deprecated·rejected Memory는 절대 전달되지 않습니다.

대화에서 기억을 제안하려면, 내용과 함께 명시적으로 `기억해` 또는 `기억해줘`로
시작합니다. 예를 들어 아래 입력은 candidate를 하나 만들고, CLI가 후보 ID를 출력합니다.
Companion은 “기억 후보로 저장했어”라고 안내하며, Voice도 같은 안내를 음성으로
재생합니다. 후보는 앞의 승인·활성화 명령을 실행하기 전까지 모델 context에 사용되지
않습니다.

```bash
docker compose run --rm dev python -m companion.cli \
  --backend fake \
  --memory-db /workspace/data/memories.sqlite \
  --prompt "기억해. 나는 Python config를 선호해"
```

일반 발화나 내용 없는 `기억해`는 후보를 만들지 않습니다. 모호한 표현 해석, 자동
교체·활성화, LLM 기반 기억 추출은 아직 제공하지 않습니다.

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

현재 저장하는 것은 순서가 있는 원문 대화 기록뿐입니다. Local CLI는 기본적으로 최근
12개 메시지와 총 4,000자 안의 기록을 다음 모델 요청에 함께 넣습니다. 필요하면
`--context-max-messages`, `--context-max-characters`로 한도를 낮출 수 있습니다.
이는 당장 대화 흐름을 잇기 위한 context입니다. 장기 기억은 명시적 저장과 lifecycle을
통해서만 관리하며, 자동 영구 저장하지 않습니다.

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
