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

## Probe order and acceptance

### 1. LLM

- Qwen3 GGUF의 정확한 변환 출처와 quantization을 선택한다.
- 한국어 prompt에 local response가 생성되는지 측정한다.
- CPU/GPU layer 설정, VRAM, first-token latency, tokens/sec를 기록한다.

### 2. STT

- 공개 한국어 fixture로 Whisper small의 transcript와 latency를 측정한다.
- 품질이 부족할 경우 Whisper medium을 비교한다.

### 3. TTS

- 공개 또는 직접 작성한 한국어 문장을 합성한다.
- 생성 시간, audio format, 한국어 고유명사·기술용어 발음을 기록한다.

각 probe는 독립 script, 재현 가능한 설치 명령, 별도 `model` 또는 `voice`
pytest marker로 관리한다. 기본 `pytest`는 weight 다운로드 없이 계속 통과해야 한다.

## Explicit non-decisions

- 지금은 speaker cloning을 선택하지 않는다.
- 모델 weight fine-tuning을 하지 않는다.
- 이 문서는 runtime adapter 구현 또는 모델 다운로드를 뜻하지 않는다.
