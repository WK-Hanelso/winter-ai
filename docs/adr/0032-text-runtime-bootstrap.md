# ADR 0032 — Text runtime의 조립 경계

- 상태: Accepted
- 날짜: 2026-09-10
- 범위: Epic #127 / T1.1 #129
- 이전 결정: [Text-first 전환과 기록 보존](0031-text-first-transition-and-history.md)

## 문제

CLI의 `run()`이 저장소·모델·Core·Worker 조립과 관리 명령, 입출력을 함께 소유했다.
다음 CharacterSnapshot/ContextAssembler 교체 때 CLI까지 함께 수정하지 않도록 조립을
분리한다. 동작 정책, 모델, DB schema와 데이터 보존 규칙은 바꾸지 않는다.

## 결정

- `application/bootstrap.py`에 Python dataclass `TextRuntimeConfig`를 둔다. argparse,
  stdin/stdout, 환경 변수 해석은 CLI에 남긴다. 기존 user_cli의 경로와 옵션은 CLI adapter가
  같은 config로 변환하며, Python 사용자는 직접 config와 ChatModel Port를 전달할 수 있다.
- `build_text_resources()`는 기존 순서대로 저장소·정체성·정책을 연다. 모델을 생성하지 않아
  history/identity 등의 관리 명령이 모델 없이 기존대로 종료할 수 있다.
- `build_text_runtime()`은 resources와 모델을 기존 `CompanionCore`에 연결한다. 현재
  turn source는 기존 `cli` 그대로다. Web/Voice runtime 이전은 이번 범위가 아니다.
- Worker는 기존 explicit opt-in일 때만 생성한다. one-shot canary는 동기 실행,
  interactive canary는 기존 scheduler/coordinator/inbox를 그대로 쓴다. 완료 메시지의
  대화 저장과 출력 callback은 CLI가 제공한다. 권한·개인정보 경계는 변경하지 않는다.
- factory는 초기화 오류를 원인과 기존 메시지가 있는 `TextRuntimeError`로 전달한다.
  모델 응답 오류는 기존 `AdapterUnavailableError`를 유지하며 fake fallback은 없다.

## 수명과 한계

현재 저장소는 메서드별 연결을 사용하고 HTTP adapter는 모델 서버 자체를 소유하지 않는다.
runtime이 모델 서버나 운영 컨테이너를 시작·중지하지 않는다. CLI는 성공/실패/EOF/exit에
`finally: runtime.close()`를 호출한다. 이 함수의 기본 대기는 0초이고 pending job이면
False를 반환한다. 기존 daemon thread를 취소하거나 종료했다고 주장하지 않는다. 필요하면
Python 호출자가 유한한 timeout을 주어 기다릴 수 있다. 영속 job/inbox의 기존 재시작 복구는
그대로이며 thread preemption/graceful Worker cancellation은 T5의 별도 범위다.

공유 `voice_profile`의 prosody metadata는 Core 계약에 남는다. 텍스트 startup에 실제
STT/TTS adapter, 장비 라이브러리, checkpoint 로딩은 필요하지 않다.

DB schema 변경이 없으므로 코드만 이전 버전으로 되돌릴 수 있다. 운영 DB·Identity·Reference는
수정하지 않고 테스트는 임시 synthetic DB로만 수행한다. 이 리팩터링은 자연스러운 대화나
장기 기억 품질이 향상됐다는 증거가 아니다.

## 검증

`tests/unit/test_text_bootstrap.py`는 config 변환, 음성 런타임 없는 startup, 모델 없는 조회,
저장소별 실패, fake fallback 차단, 재시작 뒤 identity/memory/evidence 보존, Worker opt-in,
동기/비동기 분기와 bounded close를 검증한다. 기존 CLI 및 Shared Core 회귀도 유지한다.
실행 결과는 [Text-first TODO](../text-first-implementation-todo.md)의 T1.1에 기록한다.
