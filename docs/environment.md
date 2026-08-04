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
| CUDA Toolkit | `nvcc` 10.1.243 감지 |
| Docker CLI | Docker 26.1.3, Docker Compose v5.0.1 감지 |
| Host Python | 시스템 실행 경로에서 Python 3.8.10 관찰; pyenv에 3.11.9도 존재 |

## 미확인 또는 차단된 항목

- `nvidia-smi`는 NVIDIA driver와 통신하지 못했다. GPU VRAM과 CUDA runtime
  사용 가능 여부는 확정하지 않는다.
- 조사 환경에서는 Docker daemon socket 접근이 거부됐다. 이는 실행 환경의
  격리 또는 Host 권한 설정 때문일 수 있으므로, Host 터미널에서 별도 확인한다.
- 조사 환경에서는 PulseAudio 연결과 ALSA sound card가 노출되지 않았다. 이는
  Host의 실제 데스크톱 세션에서 마이크·스피커가 없다는 증거는 아니다.
- 실제 Container에서 NVIDIA Container Toolkit runtime이 제공되는지 확인하지 않았다.

이 Issue에서는 위 문제를 해결하지 않는다. 실제 Local LLM/STT/TTS 연결 전에
각 항목을 재검증하고 결과를 갱신한다.

## Host에서 재확인할 명령

아래 명령은 Host 터미널에서 실행한다. 컨테이너 내부 결과와 혼동하지 않는다.

```bash
nvidia-smi
docker info
docker run --rm hello-world
pactl info
arecord -l
aplay -l
```

GPU Container runtime은 NVIDIA driver가 정상화된 뒤에만 확인한다.

```bash
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

## 다음 결정의 선행 조건

1. Python runtime과 컨테이너 기본 이미지 버전을 선택한다.
2. GPU를 당장 복구할지, fake adapter와 CPU 기반 검증을 먼저 끝낼지 결정한다.
3. Local LLM, STT, TTS 후보를 한국어 지원, 라이선스, VRAM, latency, streaming으로 비교한다.
