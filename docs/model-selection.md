# Local Model Selection — Milestone 0

## Decision

첫 실제 probe는 아래 순서로 수행한다. 모든 후보는 공개 GitHub 소스와 공개
가중치 배포 경로를 가진다. 외부 상용 AI API는 사용하지 않는다.

1. LLM: **Qwen3-4B-Instruct-2507**, llama.cpp + GGUF 양자화
2. STT: **Whisper small multilingual**, whisper.cpp
3. TTS: **MeloTTS Korean**

이 선택은 “최종 품질 우승자”가 아니라 RTX 2060 6 GiB에서 첫 로컬 수직 단면을
검증할 우선 probe 조합이다.

## Constraints

- Host GPU: RTX 2060, VRAM 6 GiB, CUDA compatibility 12.2
- Container 기본값: Python 3.11, CPU-only; GPU는 opt-in overlay
- 세 runtime을 동시에 GPU에 상주시킨다고 가정하지 않는다.
- 원본 음성, 가중치, 변환된 GGUF는 Git에 저장하지 않는다.

## Candidates and rationale

| Area | Priority | Alternative | Why | Source code / weights license |
| --- | --- | --- | --- | --- |
| LLM | Qwen3-4B-Instruct-2507 + llama.cpp | Qwen2.5-3B | 4B instruct 모델과 GGUF 양자화는 6 GiB GPU의 첫 local chat probe에 현실적이다. | [Qwen3 source](https://github.com/QwenLM/Qwen3), [HF model](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507): Apache-2.0 |
| STT | Whisper small + whisper.cpp | Whisper medium, faster-whisper | multilingual Whisper의 작은 checkpoint로 한국어·latency를 먼저 측정한다. | [Whisper source](https://github.com/openai/whisper), [whisper.cpp](https://github.com/ggml-org/whisper.cpp), [HF card](https://huggingface.co/openai/whisper-medium) |
| TTS | MeloTTS Korean | CosyVoice 0.5B | 첫 probe는 한국어와 CPU real-time 경로가 명시된 단순 runtime을 우선한다. | [MeloTTS source](https://github.com/myshell-ai/MeloTTS) |
| High-quality TTS | CosyVoice | — | 한국어, prosody, streaming이 강점이나 첫 수직 단면에는 의존성과 자원 부담이 크다. | [CosyVoice source](https://github.com/FunAudioLLM/CosyVoice) |

## License handling

코드 라이선스와 checkpoint 라이선스는 별도 항목으로 관리한다. Qwen의 선택
checkpoint는 Hugging Face LICENSE에서 Apache-2.0을 확인했다. Whisper와 TTS는
다운로드 직전에 선택한 정확한 checkpoint의 model card 및 LICENSE를 다시 기록한다.
라이선스가 불명확하면 probe 대상에서 제외한다.

## Executed LLM probe — 2026-08-05

선택한 LLM을 Docker 컨테이너에서 실제 실행했다. 이는 애플리케이션 adapter 연결이
아닌, Milestone 0의 독립 runtime 검증이다.

| Item | Verified value |
| --- | --- |
| Base model | [Qwen/Qwen3-4B-Instruct-2507](https://huggingface.co/Qwen/Qwen3-4B-Instruct-2507), Apache-2.0 |
| GGUF conversion | [mradermacher/Qwen3-4B-Instruct-2507-GGUF](https://huggingface.co/mradermacher/Qwen3-4B-Instruct-2507-GGUF) |
| File | `Qwen3-4B-Instruct-2507.Q4_K_M.gguf` |
| Size / SHA-256 | 2,497,280,896 bytes / `edabe01d973c31dce0d71eaf7e44628021b23b9bd2cbb93059846dad1cc4e153` |
| Runtime | llama.cpp `b10276` Vulkan release |
| Container | `winter-ai:dev` with `libgomp1`, `libvulkan1` |
| GPU result | RTX 2060 6 GiB; 37/37 layers offloaded through Vulkan |
| Korean result | one-sentence Korean response generated successfully |
| Measured result | 13.22 s process time; prompt 27.1 tokens/s; generation 10.7 tokens/s |

The GGUF is a third-party conversion. Its source repository and the original
Qwen model license are recorded separately; model files remain outside Git.

### Reproduce manually

Download the exact runtime and GGUF yourself, verify the SHA-256 above, then
run this command on the Host (not inside the development container):

```bash
docker compose build
python3 experiments/local_llm_probe.py \
  --runtime-dir /path/to/llama-b10276 \
  --model-path /path/to/Qwen3-4B-Instruct-2507.Q4_K_M.gguf
```

The script mounts both inputs read-only, requests Docker GPU access, and fails
explicitly if the runtime, model, Docker, or GPU path cannot be used. It never
downloads a checkpoint or substitutes a fake response.

### Executed CLI integration — 2026-08-06

Milestone 1의 첫 경로로, 같은 llama.cpp runtime을 long-running local server로
실행하고 `CompanionCore`의 `ChatModel` HTTP adapter를 통해 CLI와 연결했다.
CLI 컨테이너는 Compose 내부 주소 `http://llm:8080`만 사용하며, 모델 파일과 runtime은
Host에서 read-only mount된다. 실제 한국어 입력에 다음 응답을 받았다.

```text
Companion> 현재는 대화 기반의 언어 모델로 실행되고 있습니다.
```

이는 한 turn의 actual-runtime integration pass다. 현재 대화 history는 in-memory
repository에만 남고 model prompt에 이전 turn을 포함하지 않는다. SQLite 영속화와
conversation context는 후속 Milestone 1 작업이다.

## Current Orin runtime — 2026-08-15

Milestone 0의 Qwen probe와 현재 운영 endpoint는 같지 않다. `/props`로 실제 서버를
확인한 결과는 다음과 같다.

| Item | Verified value |
| --- | --- |
| Endpoint | Host SSH loopback `http://127.0.0.1:18080` |
| Server model path | `/home/armstrong/models/A.X-4.0-Light-Q4_K_M.gguf` |
| Context | 4096 tokens |
| First token / full response | 0.439 s / 1.455 s on the measured continuity prompt |
| Privacy | Orin loopback only; Host에서 SSH tunnel로 접근 |

기존 `./winter chat`은 문서와 달리 PC의 CPU-only llama-server를 호출해 같은 종류의
문맥에서 first token 13.846초, full response 19.446초가 걸렸다. launcher와 모든 live
interface 기본값을 Orin tunnel로 통일했다.

`A.X-4.0-Light`의 정확한 upstream model card와 checkpoint license는 이 저장소에서 아직
검증되지 않았다. 개인 local runtime 사용과 별개로, checkpoint를 재배포하거나 결과를
최종 모델 선택 근거로 확정하기 전에는 provenance와 license를 반드시 추가 확인한다.

## Winter Executive 후보 재검토 — 2026-08-18

Worker를 직접 답변자로 쓰지 않고 Winter가 `IntentBrief`와 `WinterDecision`을 소유하기
위해 단일 Core Model의 역할을 확장한다. 일반 benchmark가 아니라
[Work Orchestration](work-orchestration.md)의 동일한 held-out scene으로 비교한다.

실제 Orin을 읽기 전용 조사한 결과 Jetson AGX Orin Developer Kit 32GB, aarch64,
available memory 약 24.8GB, available disk 약 119GB다. 현재 llama.cpp는
version 문자열은 `0.1.0-dev build 1`이지만 source는 2026-08-13의 commit
`8e7f22b67ef4667b4ddd50230771287f328cfb3f`다. 초기에는 version 문자열만 보고 오래된
runtime으로 오판했으나 실제 commit 날짜를 확인해 철회했다. 최신 source `01818e4`의 CUDA
configure도 별도 경로에서 성공했지만 기존 runtime이 5일 전 source임을 확인한 뒤 중복
build는 중단했다.

실행 순서는 Qwen3-14B Q4, Gemma 3 12B IT Q4, Qwen3-30B-A3B-Instruct-2507 Q4,
Gemma 3 27B Q4였고 모두 평가를 완료했다.
Qwen3.5-9B는 최신성이 아니라 실제 aarch64/llama.cpp 안정성 때문에 보류한다. 모델별
공식 source, license, runtime 위험과 activation gate는 Work Orchestration 문서에 기록했다.
첫 artifact는 Qwen 공식 `Qwen/Qwen3-14B-GGUF`의 9GB
`Qwen3-14B-Q4_K_M.gguf`이며, 운영 server를 교체하지 않고 별도 port에서 평가한다.

### Executed Winter Executive probe — 2026-08-18

| Model | Structured checks | Mean call latency | Generation | 판정 |
| --- | ---: | ---: | ---: | --- |
| Qwen3-14B Q4_K_M, non-thinking V2.1 | 38/47 | 11.6초 | 미재측 | 8/8 schema, 안전 경계 통과, ambiguity·review 의미 실패 |
| A.X-4.0-Light Q4_K_M, non-thinking V2.1 | 27/47 | 7.7초 | 미재측 | 6/8 schema, 안전 경계 통과, provenance·review 의미 실패 |

Qwen3 기본 thinking은 첫 scene을 150초 이상 완료하지 못해 중단했다. V2.1
non-thinking은 intent+review 직렬 overhead가 약 23.1초로 줄었지만 실시간 대화에는 아직
느리다. deterministic 계약은 private leak·권한 초과·불완전 report answer를 막았다.
모델 선택은 Qwen > A.X지만 Qwen도 ambiguity와 Worker report 평가 gate에 실패했다. 상세는
[Work Orchestration Evaluation](work-orchestration-evaluation.md)에 있다.

### Expanded semantic-v2 probe — 2026-08-18

Qwen3-14B에 새 Intent 6개·Review 6개를 prompt 수정 없이 추가했다. 46/66,
schema 11/12, 평균 10.7초였고 ambiguity 0/3, network scope 0/1, review assessment
3/6으로 의미 gate에 다시 실패했다.

공식 Google [`gemma-3-12b-it-qat-q4_0-gguf`](https://huggingface.co/google/gemma-3-12b-it-qat-q4_0-gguf)는
QAT Q4_0·12B·약 8.07GB이며 파일 접근 전
[Gemma 사용 약관](https://ai.google.dev/gemma/terms) 수락이 필요하다. llama.cpp 공식
조직의 [`ggml-org/gemma-3-12b-it-GGUF`](https://huggingface.co/ggml-org/gemma-3-12b-it-GGUF)
Q4_K_M은 public resolve가 가능하고
7,300,574,976 bytes이며 license는 동일한 Gemma다.

천우는 2026-08-18 개인 로컬 평가를 위해 Gemma Terms에 명시적으로 동의했다. 비교
quant를 Qwen과 맞추기 위해 llama.cpp 공식 조직의 Q4_K_M을 내려받았고 SHA-256
`7bb69bff3f48a7b642355d64a90e481182a7794707b3133890646b1efa778ff5`를 확인했다.
Hugging Face repository revision은 `ec0cbabd8dbff316f659876a50202295c3c4a314`다.
운영 `:8080`과 분리된 `:8081`에서 smoke와 동일 semantic-v2를 실행한 뒤 서버를 종료했다.

| Model | Score | Schema | Mean | Ambiguity | Read-only ready | Network blocked | Review assessment | Action | Evidence IDs |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Qwen3-14B Q4_K_M | 46/66 | 11/12 | 10.7초 | 0/3 | 2/2 | 0/1 | 3/6 | 5/6 | 6/6 |
| Gemma 3 12B IT Q4_K_M | 40/66 | 11/12 | 15.2초 | 0/3 | 0/2 | 1/1 | 3/6 | 4/6 | 6/6 |
| Qwen3-30B-A3B Instruct 2507 Q4_K_M | 46/66 | 12/12 | 5.5초 | 0/3 | 1/2 | 0/1 | 3/6 | 5/6 | 6/6 |
| Gemma 3 27B IT Q4_K_M | 44/66 | 11/12 | 27.0초 | 0/3 | 1/2 | 0/1 | 3/6 | 5/6 | 6/6 |

Gemma는 인터넷 요청을 차단했지만 `network.access`가 아니라 모든 Intent의 resource를
`account`로 잘못 분류해 우연히 차단했다. 모호한 세 요청을 하나도 되묻지 않았고 명확한
읽기 요청도 두 건 모두 실행 가능하게 만들지 못했다. Review에서는 Qwen과 마찬가지로
회귀 실패와 사용자 선택을 `supported`로 오판했다. 따라서 현재 계약의 상대 우위는
Qwen3-14B지만 **두 모델 모두 activation 실패**다.

이는 Gemma를 별도 prompt·sampling으로 최적화한 절대 성능 비교가 아니다. 현재 겨울이
계약을 수정하지 않고 같은 adapter·seed·scene에 투입했을 때의 교체 가능성 평가다.
모델마다 contract를 다시 맞추면 held-out 비교가 오염되므로 이번 선택에는 동일 조건
결과를 사용한다.

### Qwen3-30B-A3B Instruct 2507 probe — 2026-08-19

Orin에 이미 있던
[`unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF`](https://huggingface.co/unsloth/Qwen3-30B-A3B-Instruct-2507-GGUF)의
`Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf`를 새로 다운로드하지 않고 검증했다. 원본
Qwen model은 Apache-2.0이고 GGUF는 third-party conversion이다. 실제 크기
18,556,686,752 bytes와 SHA-256
`6c997b8af17debdfb01d890214400ccbab00db6acc0ba8da5de1cc906c4774d0`가 공개 artifact와
일치했다.

운영 A.X `:8080`을 유지한 채 같은 llama.cpp commit `8e7f22b`에서 `-ngl 99 -c 4096
-np 1`, loopback `:8081`로 기동했다. 24.7초에 load를 마쳤고 두 모델 동시 실행 중
available memory는 약 4.5GiB까지 줄었지만 OOM은 없었다. 평가 뒤 임시 server와 tunnel을
종료했고 A.X health와 available memory 약 19GiB를 재확인했다.

semantic-v2는 46/66·schema 12/12·평균 5.5초로 Qwen3-14B보다 빨랐지만 의미 gate는
나아지지 않았다. ambiguity 0/3, 올바른 network scope 0/1, review assessment 3/6이었다.
여섯 Intent 중 다섯 개의 resource를 `account`로 골라 명확한 read-only도 1/2만 ready가
됐다. 이는 안전한 차단이 아니라 과다 차단과 의미 오분류다.

실제 Winter Core의 Identity·Memory·재시작 7턴은 기존 자동 check 14/14를 통과했지만 사람
검토에서 교체 근거가 없었다. Qwen은 화면 밖에서 차를 마시겠다는 몸의 행동을 만들고,
목소리와 대화 기억의 우선순위 질문을 `네 목소리`로 바꿔 답했다. 같은 경로의 평균은 Qwen
1.81초, 운영 A.X 1.47초였고 A.X가 질문 의미와 grounding을 더 잘 보존했다. 따라서 자동
14/14는 grounding과 의미 보존을 보장하지 않으며 Qwen3-30B-A3B를 대화 또는 Worker 지휘
Core로 activation하지 않는다.

### Gemma 3 27B probe — 2026-08-19

Google 공식 QAT Q4_0 artifact는 Hugging Face token이 필요한 gated file이라 자동
다운로드하지 않았다. 천우의 기존 Gemma Terms 동의 범위에서, 12B 비교와 같은 llama.cpp
공식 조직의 [`ggml-org/gemma-3-27b-it-GGUF`](https://huggingface.co/ggml-org/gemma-3-27b-it-GGUF)
Q4_K_M을 평가했다. 실제 크기 16,546,404,736 bytes와 SHA-256
`edc9aff4d811a285b9157618130b08688b0768d94ee5355b02dc0cb713012e15`가 공개 artifact와
일치했다.

운영 A.X와 동시에 약 24.3초에 load됐고 available memory는 약 4.7GiB였다. semantic-v2는
44/66·schema 11/12·평균 27.0초였다. ambiguity 0/3, read-only ready 1/2, 올바른 network
scope 0/1, review assessment 3/6으로 12B의 핵심 실패를 해결하지 못했다. 회귀 `fail`, 천우의
최종 선택과 2개 표본 한계를 다시 `supported`로 오판했다.

실제 Winter Core 대화 평가는 첫 턴부터 HTTP 400으로 중단됐다. 현재 Core의 연속 role
message를 Gemma chat template이 `user/assistant` 교대 위반으로 거절했다. adapter에서
message를 합칠 수는 있지만 Worker 의미 gate와 latency가 이미 실패했으므로 모델별 호환
코드를 추가하지 않았다. 평가 artifact는 Orin에 보존하고 운영 설정에는 연결하지 않는다.

이번 두 대형 후보로 **같은 12~30B급 drop-in을 계속 순회하는 전략은 중단**한다. parameter
수 증가는 모호성 판단, grounding이나 Worker 검수를 자동으로 해결하지 않았고 Gemma dense
27B는 실시간 latency도 만족하지 못했다. 다음 후보는 새 모델 계열 또는 runtime 변경이 이
실패 축을 해결한다는 근거가 있을 때만 같은 locked suite로 평가한다.

## Probe order and acceptance

### 1. LLM

- Qwen3 GGUF의 정확한 변환 출처와 quantization을 선택한다.
- 한국어 prompt에 local response가 생성되는지 측정한다.
- CPU/GPU layer 설정, VRAM, first-token latency, tokens/sec를 기록한다.

### 2. STT

- 공개 한국어 fixture로 Whisper small의 transcript와 latency를 측정한다.
- 품질이 부족할 경우 Whisper medium을 비교한다.

#### Executed STT probe — 2026-08-05

`whisper.cpp`의 공식 Docker image와 Whisper small multilingual checkpoint를
선택했다. `faster-whisper`는 CTranslate2, CUDA, cuDNN version alignment가 추가로
필요하므로 비교 후보로 남겼다.

| Item | Verified value |
| --- | --- |
| Runtime | [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp), official `main-vulkan` Docker image |
| Checkpoint | `ggerganov/whisper.cpp` `ggml-small.bin` (multilingual) |
| Checkpoint SHA-256 | `1be3a9b2063867b937e64e2ec7483364a79917e157fa98c5d94b5c1fffea987b` |
| Fixture | [Zeroth-Korean test split](https://huggingface.co/datasets/kresnik/zeroth_korean), CC BY 4.0; 10.4535 s, 16 kHz mono FLAC |
| Reference text | `몬터규는 자녀들이 사랑을 제대로 못 받고 크면 매우 심각한 결과가 초래된다는 결론을 내렸습니다` |
| CPU result | local Korean transcript generated in 10.03 s (real-time factor 0.96) |
| CUDA result | blocked before start: official image requires CUDA >=13.0, while Host driver 535 supports up to CUDA 12.2 |

The generated CPU transcript had several word substitutions, so this is a
runtime and latency pass, not a Korean accuracy acceptance pass. The fixture,
audio, and checkpoint are stored outside Git.

Run the explicit CPU probe on the Host with:

```bash
python3 experiments/stt_probe.py \
  --model-path /path/to/ggml-small.bin \
  --audio-path /path/to/korean-fixture.flac
```

Use `--device cuda` only to request the CUDA image. It does not fall back to
CPU if Docker GPU initialization fails.

#### Phone runtime update — 2026-08-17

Three real phone turns showed that the warmed Whisper service still spent a
median 3.79 seconds inside STT. Thread tuning from 4 to the CPU's 6 physical
cores improved synthetic throughput, but did not approach the interaction gate.
The official whisper.cpp stream example is a repeated microphone-window demo,
not a streaming upload endpoint for the current HTTP server.

SenseVoiceSmall Q8 GGUF was therefore evaluated as a separate CPU runtime. The
official AVX2 binary needs a newer glibc than the Host, so it runs inside Ubuntu
24.04 and remains behind the existing `SpeechToText` port.

| Item | Verified value |
| --- | --- |
| Runtime | [SenseVoice llama.cpp runtime v0.1.9](https://github.com/QwenAudio/SenseVoice/releases/tag/runtime-llamacpp-v0.1.9), AVX2 CPU binary |
| Model | [SenseVoiceSmall-GGUF Q8](https://huggingface.co/FunAudioLLM/SenseVoiceSmall-GGUF), repository declares Apache-2.0 |
| Short synthetic | 2.36–2.56 s audio, 0.12–0.13 s model time |
| Zeroth-Korean fixture | 10.4535 s, 0.49 s, exact reference-text match |
| Full Web STT | 0.347 s including ffmpeg, HTTP and model for 2.36 s WAV |
| GPU use | none; preserves the nearly full 6 GB GPU for TTS and Voice Conversion |

This activates SenseVoice only for limited phone operation. It does not claim
general Korean superiority from four utterances. A new 20-turn phone baseline
and held-out names, technical terms, noise and spontaneous speech remain the
quality gate. Whisper stays available only through explicit backend selection;
there is no silent fallback. Artifact hashes and the operational decision are
recorded in [ADR-0008](adr/0008-sensevoice-phone-stt.md).

### 3. TTS

- 공개 또는 직접 작성한 한국어 문장을 합성한다.
- 생성 시간, audio format, 한국어 고유명사·기술용어 발음을 기록한다.

#### Executed TTS probe — 2026-08-06

MeloTTS Korean을 첫 runtime으로 선택했다. 공식 GitHub source와 Hugging Face
checkpoint는 MIT license이며, Korean `KR` speaker와 CPU 실행 예시를 제공한다.
CosyVoice는 한국어·streaming·prosody 품질을 갖춘 다음 비교 후보로 남긴다.

| Item | Verified value |
| --- | --- |
| Runtime / model | [myshell-ai/MeloTTS](https://github.com/myshell-ai/MeloTTS) commit `2091453`; [MeloTTS-Korean](https://huggingface.co/myshell-ai/MeloTTS-Korean), MIT |
| Execution | pinned Python 3.9 Docker probe image; explicit CPU |
| Input | `안녕하세요. 로컬 컴패니언의 음성 출력 경로를 확인하고 있습니다.` |
| Output | 44.1 kHz mono `pcm_s16le` WAV, 6.389048 s |
| WAV SHA-256 | `e47c498cd131c2efd81511bb1d8b9abf4abf9c714c7fa5ebe1cebd86f8333a0f` |

Run manually on the Host after building the image:

```bash
docker build -f Dockerfile.melotts-probe -t winter-ai:melotts-probe .
python3 experiments/tts_probe.py \
  --cache-dir /path/outside/git/melotts-cache \
  --output-path /path/outside/git/melotts-korean.wav
```

The first run downloads the checkpoint to the supplied cache directory. The
script never uses a remote conversational API and generated audio remains out
of Git. This probe validates runtime output, not a final Companion voice or
prosody evaluation.

각 probe는 독립 script와 재현 가능한 설치 명령으로 관리한다. 실제 LLM probe는
명시적 수동 실행이며 기본 `pytest`는 weight 다운로드 없이 계속 통과해야 한다.

## Explicit non-decisions

- 지금은 speaker cloning을 선택하지 않는다.
- 모델 weight fine-tuning을 하지 않는다.
- 이 문서는 runtime adapter 구현 또는 모델 다운로드를 뜻하지 않는다.
