# ADR-0001: Docker 개발 환경 기준

- 상태: Accepted
- 날짜: 2026-08-04
- 관련 Issue: #4

## 맥락

`winter-ai`는 Docker-first 프로젝트다. Host는 Docker Engine, NVIDIA driver,
GPU, PulseAudio와 물리 오디오 장치를 제공하고, 프로젝트 의존성과 애플리케이션은
Container에서 실행한다.

Host 검증 결과는 RTX 2060 (VRAM 6 GiB), NVIDIA driver 535.230.02, CUDA 12.2
호환 상한, 등록된 `nvidia-container-runtime`, PulseAudio와 ALSA 장치다. 그러나
기본 테스트는 GPU·마이크·인터넷·모델 checkpoint 없이 동작해야 한다.

## 결정

1. 첫 개발 Container의 언어 runtime은 **Python 3.11**로 한다.
2. 첫 기본 이미지 계열은 공식 **`python:3.11-slim-bookworm`**으로 한다.
3. 기본 Compose 구성은 CPU-only 개발·테스트 환경으로 둔다.
4. GPU와 Voice는 기본 서비스에 암묵적으로 포함하지 않는다.
   - GPU는 별도 Compose overlay 또는 명시적 profile에서만 `gpus: all`을 요청한다.
   - Voice는 별도 Compose overlay 또는 명시적 profile에서만 PulseAudio socket과
     필요한 ALSA 장치를 전달한다.
5. 소스 코드는 개발 시 bind mount로 Container에 전달한다. 대화 DB, 생성 오디오,
   모델 checkpoint와 speaker reference도 이미지에 넣지 않고 Host의 Git-ignored
   경로에서 명시적으로 mount한다.
6. Host의 Python 3.8/3.11 설치와 Host CUDA Toolkit 10.1은 Container 의존성의
   기준으로 사용하지 않는다. GPU Container는 Host NVIDIA driver가 제공하는
   CUDA 호환 범위 안에서 자체 runtime을 사용한다.

## 근거

- Python 3.11은 기존 Host pyenv에도 존재하고, Python AI 생태계의 호환성과
  장기 유지보수 사이에서 보수적인 기준이다.
- `slim-bookworm`은 작은 기본 이미지이면서 Debian 계열의 명시적인 시스템
  패키지 기반을 제공한다. 추후 오디오·빌드 의존성이 필요해져도 추가 항목을
  Dockerfile에서 추적할 수 있다.
- RTX 2060의 6 GiB VRAM은 작은 양자화 모델과 일부 음성 실험에는 유용하지만,
  모든 개발·테스트의 필수 조건으로 삼기에는 제한적이다.
- CPU 기본값은 AGENTS.md의 오프라인 테스트 요구사항과 실제 모델 실패를 fake로
  숨기지 않는 원칙을 함께 만족시킨다.
- GPU·오디오 전달을 opt-in으로 분리하면 일반 개발이 장치 권한이나 데스크톱
  세션에 불필요하게 결합되지 않는다.

## 고려한 대안

### Host Python을 직접 사용

기각했다. Host Python 버전과 설치 패키지가 재현성을 보장하지 못하며,
Docker-first 원칙과 맞지 않는다.

### GPU를 기본 Compose 서비스에 포함

기각했다. GPU가 없는 환경에서도 단위 테스트가 돌아야 하고, 6 GiB VRAM은
모든 runtime을 항상 수용하지 못한다.

### `python:3.11-slim`처럼 Debian release를 생략한 태그 사용

기각했다. 편리하지만 기반 OS 계열이 시간이 지나며 바뀔 수 있어, 시스템
의존성 문제의 재현성과 조사 가능성이 낮아진다.

### CUDA 개발 이미지를 기본 이미지로 사용

기각했다. 기본 테스트 및 CPU 작업에 불필요하게 이미지 크기와 GPU 의존성을
도입한다. GPU runtime은 실제 Local LLM/STT/TTS adapter가 선택된 뒤 별도
profile에서 다룬다.

## 결과와 다음 작업

다음 구현 Issue에서 이 ADR에 따라 다음만 만든다.

- CPU 기본 Dockerfile 및 Compose 설정
- 명시적 GPU/Voice overlay의 빈 구조
- Container 내부에서 실행하는 최소 오프라인 검증 명령

그 Issue에서도 실제 모델 checkpoint 다운로드와 실제 모델 연결은 하지 않는다.
