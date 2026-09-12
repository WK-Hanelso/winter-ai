# ADR 0042 — Text CLI의 호스트 Docker 분할 추론 경로

상태: 로컬 텍스트 실행 경로에 적용. 대화 품질 승격과는 별개다.

## 결정과 이유

현재 모델과 Shared Core/Identity/DB를 바꾸지 않고, 검증한 CPU expert offload와
GPU 추론 배치를 `./winter text`에 연결한다. 작은 모델로의 묵시적 교체나 원격 서버
자동 복구 대신 명시적인 호스트 Docker 서버를 사용한다. Voice와 Worker 기능 확장은 없다.

- `127.0.0.1:18081`, `winter-text-llm` 컨테이너 하나를 시작하고 health 이후 Core를 연다.
- 동일 프로필·프로젝트 소유권을 확인하고 시작/종료를 파일 잠금으로 직렬화한다.
- 이미지 ID를 해석해 그 artifact를 실행한다. 모델은 read-only, 로그는 최대 5MB × 2개다.
- CPU 4, RAM/swap 합계 한도 20GiB, context 4096, parallel 1, cpu-moe 44,
  batch 128/ubatch 32, CUDA graph 비활성화 프로필을 사용한다.
- `/exit`는 대화창만 종료하고 `./winter stop`은 모델 자원도 반환한다. 부팅 자동 실행은 없다.
- 명시적 별도 모델 주소는 연결 상태만 확인한다. Orin에 SSH하거나 fake로 전환하지 않는다.
- 개발 전용 data 디렉터리 override로 정상 CLI 자체를 개인 DB와 분리해서 검증한다.

## 대안과 제약

기존 임시 서버 스크립트를 별도 터미널에서 켜는 방법은 유지하지만 정상 사용의 필수 절차로
삼지 않는다. 이전 Host CLI/Voice 경로는 보존한다. 분할 추론은 context prefill이 느릴 수
있으며 이 연결만으로 대화 자연스러움이 개선되는 것은 아니다. 모델/이미지가 준비되지 않은
장비는 명시적 오류로 멈춘다. 평가 원문·개인 기록·장비 진단은 로컬에만 보관한다.

검증 계약: `tests/unit/test_text_llm_lifecycle.py`, `tests/unit/test_text_launcher.py`.
