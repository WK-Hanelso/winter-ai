# ADR-0016 — Typed Read-only Project Gateway Before Provider Connection

- 상태: Accepted, Shadow only
- 날짜: 2026-08-18

## Context

Qwen은 외부 인터넷 검색을 `project.inspect/project`로 잘못 분류했고 Gemma는 모든
Intent resource를 `account`로 분류했다. 따라서 모델이 붙인 capability label을 검사하는
것만으로 실제 권한을 보장할 수 없다.

기존 Codex V0는 sanitized bundle과 `--sandbox read-only`를 사용한다. OpenAI 공식
[Sandbox 문서](https://learn.chatgpt.com/docs/sandboxing)는 sandbox가 spawned command의
파일·network 경계를 OS 수준에서 강제하며 Linux에서 bubblewrap을 사용한다고 설명한다.
그러나 read-only는 host의 민감 파일이 Worker에게 전혀 보이지 않는 project-only read를
뜻하지 않는다. Codex control plane은 인증과 API 통신도 필요하므로 이를 Agent tool과
분리하지 않은 채 기존 route를 새 gateway로 선언하지 않는다.

## Decision

Provider와 모델에 독립적인 `ProjectToolGateway` Port와 `ProjectReadGateway` adapter를
추가한다.

- `WorkerTaskPacket`이 정확히 `project.inspect`와 `project`일 때만 세션을 만든다.
- Worker가 요청할 수 있는 operation은 `list_files`, `search_text`, `read_text`뿐이다.
- shell, command, URL, network, account와 write operation은 계약에 존재하지 않으며
  알 수 없는 operation은 fail-closed한다.
- 절대경로, `..`, 역슬래시, symlink, 숨김 파일, binary, 512KB 초과 파일을 거부한다.
- 한 번에 최대 200줄, 검색 최대 50건으로 출력 크기를 제한한다.
- 시작 시 safe text snapshot의 SHA-256을 고정하고 매 call과 실제 read 시점에 다시
  확인한다. 바뀐 bundle은 읽지 않는다.
- 읽은 각 줄은 locator와 content hash에서 파생한 stable evidence ID를 받는다.
- allowed/denied call, reason과 evidence ID를 session audit에 남긴다.
- 최종 `EvidenceReport`의 evidence ID와 locator는 해당 gateway session이 발급한
  catalog의 부분집합이어야 한다.

## Consequences

모델이 network 작업을 project 읽기로 잘못 부르더라도 실제 gateway에는 network operation이
없다. Worker가 `/etc`, `.env`, Memory DB나 다른 host 경로를 요청하거나 report에 가짜
locator를 넣어도 deterministic하게 차단할 수 있다.

현재 구현은 offline Shadow 계약이다. 기존 Codex CLI route는 아직 이 Port를 호출하지
않으며 사용자-facing 동작도 바뀌지 않는다. 다음 provider adapter는 arbitrary shell 대신
typed call만 왕복시키고, 인증 credential과 control-plane network를 Agent tool process에서
분리해야 한다. 이 live isolation probe와 실제 20개 read-only task audit 전에는 새
Orchestration route를 활성화하지 않는다.
