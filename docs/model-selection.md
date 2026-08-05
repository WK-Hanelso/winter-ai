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

### 3. TTS

- 공개 또는 직접 작성한 한국어 문장을 합성한다.
- 생성 시간, audio format, 한국어 고유명사·기술용어 발음을 기록한다.

각 probe는 독립 script와 재현 가능한 설치 명령으로 관리한다. 실제 LLM probe는
명시적 수동 실행이며 기본 `pytest`는 weight 다운로드 없이 계속 통과해야 한다.

## Explicit non-decisions

- 지금은 speaker cloning을 선택하지 않는다.
- 모델 weight fine-tuning을 하지 않는다.
- 이 문서는 runtime adapter 구현 또는 모델 다운로드를 뜻하지 않는다.
