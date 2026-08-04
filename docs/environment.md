# Host 및 Container 환경 기준

이 문서는 `winter-ai`가 실행될 Host 환경의 검증된 사실과, 컨테이너가 맡을
책임을 구분한다. 측정 시점의 자원 사용량은 달라질 수 있으므로 정적 사양과
실행 시점 관찰을 구분한다.

## 책임 경계

| 구분 | 책임 |
| --- | --- |
| Host | Linux kernel, Docker Engine, NVIDIA driver, GPU, 마이크·스피커 및 오디오 서버 제공 |
| Container | 고정된 Python 버전, 프로젝트 의존성, 애플리케이션, 테스트와 로컬 모델 runtime 실행 |
| Bind mount | 소스 코드와 사용자가 명시적으로 공유한 개발 데이터만 전달 |
| GPU / audio 전달 | 기본 가정이 아니다. 필요한 profile 또는 실행 명령에서 장치·소켓·권한을 명시적으로 전달 |

따라서 Host의 시스템 Python, CUDA Toolkit 또는 오디오 라이브러리를 프로젝트
의존성으로 직접 사용하지 않는다. Container는 Host driver 및 장치를 소비할 수
있어야 하지만, 이들이 정상이라는 가정 위에서 설계하지 않는다.

## 확인된 Host 사양

| 항목 | 관찰 결과 |
| --- | --- |
| OS | Ubuntu 20.04.6 LTS, Linux 5.15.0-139-generic, x86_64 |
| CPU | Intel Core i7-8750H, 6 cores / 12 threads |
| 메모리 | 총 31 GiB; 조사 당시 약 25 GiB 사용 가능 |
| 저장소 | ext4 약 916 GiB, 조사 당시 약 371 GiB 여유 |
| GPU hardware | NVIDIA GeForce RTX 2060 Mobile (TU106M) 감지 |
| NVIDIA driver | 535.230.02; GPU VRAM 6,144 MiB 중 조사 당시 5,915 MiB 여유 |
| CUDA 호환 상한 | `nvidia-smi`가 CUDA 12.2를 보고 |
| CUDA Toolkit | Host `nvcc` 10.1.243 감지; Container의 CUDA runtime 선택 근거로 사용하지 않음 |
| Docker CLI | Docker 26.1.3, Docker Compose v5.0.1 감지 |
| Host Python | 시스템 실행 경로에서 Python 3.8.10 관찰; pyenv에 3.11.9도 존재 |

## 직접 검증한 Host runtime

| 항목 | 검증 결과 | 설계상 의미 |
| --- | --- | --- |
| Docker Engine | Docker server 26.1.3에 접근 가능 | Container 기반 개발을 시작할 수 있음 |
| NVIDIA runtime | Docker runtime 목록에 `nvidia-container-runtime` 등록 | 이후 GPU Compose profile을 별도 제공할 수 있음 |
| NVIDIA GPU | RTX 2060, driver 535.230.02, CUDA 12.2 호환 상한 | 6 GiB VRAM 내에서 실행 가능한 모델을 선택해야 함 |
| PulseAudio | local server와 기본 sink/source 확인 | Voice 컨테이너는 PulseAudio socket 전달 전략이 필요함 |
| ALSA | PCH ALC1220 analog capture/playback 장치 확인 | 초기 push-to-talk 입력·출력의 Host 장치 기반이 존재함 |

## 아직 검증하지 않은 항목

- 실제 GPU 컨테이너에서 `nvidia-smi`가 동작하는지 확인하지 않았다. 이 작업은
  컨테이너 이미지 pull을 수반하므로 Host 준비 상태 조사 범위에서 제외했다.
- 컨테이너에서 PulseAudio socket과 ALSA 장치 전달이 실제 녹음·재생까지
  동작하는지는 확인하지 않았다.
- 현재 기본 마이크·스피커가 사용자 의도와 맞는 장치인지는 실제 voice round-trip
  구현 단계에서 확인한다.

실제 Local LLM/STT/TTS 연결 전에는 위 항목을 해당 adapter의 integration test로
재검증하고 결과를 갱신한다.

## Host 진단 명령

아래 명령은 Host 터미널에서 실행했으며, 컨테이너 내부 결과와 혼동하지 않는다.

```bash
nvidia-smi
docker info
docker run --rm hello-world
pactl info
arecord -l
aplay -l
```

GPU Container runtime은 첫 GPU profile 이미지가 선택된 뒤 확인한다.

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## 다음 결정의 선행 조건

1. Python runtime과 컨테이너 기본 이미지 버전을 선택한다.
2. GPU를 당장 복구할지, fake adapter와 CPU 기반 검증을 먼저 끝낼지 결정한다.
3. Local LLM, STT, TTS 후보를 한국어 지원, 라이선스, VRAM, latency, streaming으로 비교한다.
