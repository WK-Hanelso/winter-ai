# ADR-0017 — Separate Codex Control Plane from Model-visible Project Tools

- 상태: Accepted, Shadow only
- 날짜: 2026-08-18

## Context

ADR-0016의 typed project gateway는 filesystem 권한을 deterministic하게 제한했지만 실제
provider와 연결되지 않았다. Codex는 인증과 OpenAI 통신을 위해 credential·network가 필요한
반면, 모델이 호출하는 도구에는 그 권한이 보이면 안 된다. 기존 `--sandbox read-only`만으로는
이 두 역할이 분리됐다고 볼 수 없다.

OpenAI 공식 [Codex MCP 문서](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)는 stdio
server의 command/args/env와 `enabled_tools` allowlist를 지원한다. 공식
[Config Reference](https://learn.chatgpt.com/docs/config-file/config-reference)는 기본
`shell_tool`과 `unified_exec`가 활성이고, `web_search = "disabled"`가 검색 도구를 제거한다고
명시한다. 따라서 provider transport와 model-visible tool을 별도 process boundary로 둔다.

## Decision

`CodexTypedProjectWorker`와 로컬 stdio `ProjectGatewayMcpServer`를 Shadow adapter로 추가한다.

- Codex 부모 process만 임시 `CODEX_HOME/auth.json`을 받아 provider 인증과 API 통신을 한다.
- 사용자 config·MCP·plugin을 상속하지 않도록 매 실행마다 빈 임시 `CODEX_HOME`을 만들고
  regular `auth.json` 하나만 복사한다.
- Codex agent의 working directory는 빈 directory다. sanitized project bundle은 별도 경로에
  두므로 `AGENTS.md`나 project file이 MCP 밖에서 자동 주입되지 않는다.
- `shell_tool`, `unified_exec`, `shell_snapshot`, plugin, app, multi-agent와 관련 discoverable
  도구를 명시적으로 끄고 web search와 image read도 제거한다.
- 유일한 project data path는 `winter_project` stdio MCP다. Codex 설정의 `enabled_tools`는
  `list_files`, `search_text`, `read_text` 세 개만 허용한다.
- MCP process는 `/usr/bin/env -i`로 시작한다. `LANG`, `PYTHONUTF8`, local code import용
  `PYTHONPATH`만 받고 `CODEX_HOME`, `HOME`, proxy, token, API key를 받지 않는다. server도
  예상 밖 environment key가 있으면 시작을 거부한다.
- MCP의 `tools/call`은 기존 `ProjectReadGateway`로만 변환한다. 알 수 없는 tool, network,
  write와 path escape는 같은 fail-closed audit 경로로 들어간다.
- Codex 결과는 `EvidenceReport` schema로 받고, MCP state의 allowed/denied audit과 evidence
  catalog가 없으면 실패한다. 보고서의 evidence ID와 locator가 catalog와 정확히 맞아야만
  반환한다.
- 기존 CLI·Web·Voice의 explicit Codex V0 route는 바꾸지 않는다.

MCP stdio 구현은 공식 [MCP transport specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/transports)의
newline-delimited JSON-RPC와 initialize/tools lifecycle만 구현한다. resource, prompt, sampling,
elicitation과 network transport는 제공하지 않는다.

## Verification

- gateway·provider 경계 오프라인 테스트 17개 통과
- 실제 `env -i` subprocess에서 initialize, tools/list, read_text와 state audit 통과
- gpt-5.4 synthetic live probe 1회 통과
  - 외부 전달 데이터: 개인정보 없는 3줄 README와 synthetic WorkerTaskPacket
  - 약 15.3초
  - tool audit: `list_files` 1회, `read_text` 1회, denied 0
  - report evidence 3개가 session catalog의 ID·locator와 모두 일치

재현 명령:

```bash
PYTHONPATH=src /home/hanelso/.local/bin/python3.11 \
  experiments/codex_gateway_probe.py
```

## Consequences

prompt injection을 받은 모델이 shell이나 web tool을 고를 수 없고, MCP에 임의 operation이나
project 밖 path를 보내도 gateway가 거부한다. 인증 정보는 Codex provider client에는
필요하지만 model-visible MCP process에는 전달되지 않는다.

이 경계는 **모델의 도구 권한**을 제한한다. 신뢰한 Codex CLI binary 자체가 탈취됐을 때
부모 process의 인증 정보를 보호하는 별도 security sandbox는 아니다. 또한 synthetic 1건은
prompt-injection 공격 suite, 실제 repository 20 task, latency·rate limit·중단 복구와 사람
유용성 평가를 대신하지 않는다. 따라서 adapter는 Shadow에만 두고 기존 사용자-facing
route나 WinterDecision에 아직 연결하지 않는다.
