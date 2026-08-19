# ADR-0018 — Run Codex Changes in a Disposable Copy and Stop Before Adoption

- 상태: Accepted, Shadow only
- 날짜: 2026-08-18

## Context

기존 Codex V0와 typed project gateway는 실제 전문 분석을 수행하지만 project를 수정하거나
테스트할 수 없다. 천우가 원하는 실용 경로에는 코드 변경이 필요하다. 그러나 운영 중인 혼합
worktree에 Worker를 바로 연결하면 구현 승인과 결과 채택이 섞이고, 잘못된 수정이 사용자와
다른 Agent의 작업을 덮을 수 있다.

OpenAI 공식 [sandbox 문서](https://learn.chatgpt.com/docs/sandboxing)는
`workspace-write`에서 현재 workspace 안의 편집과 command 실행을 허용하고 command network는
기본 차단한다고 설명한다. 공식
[non-interactive 문서](https://learn.chatgpt.com/docs/non-interactive-mode)는
`codex exec --sandbox workspace-write`, JSON Schema 결과와 JSONL event 출력을 지원한다.
이 runtime 경계를 그대로 실제 저장소에 주는 대신 개인정보 없는 폐기 가능한 복사본 안에
한정한다.

## Decision

`CodexIsolatedWorkWorker`를 Shadow adapter로 추가한다.

1. `ImplementationAuthorization`은 구현 승인 ID, 목적, 정확한 수정 허용 파일, 고정 검증
   profile과 최대 변경 line 수를 가진다. `pending` 요청, 절대·상위·숨김 경로는 거부한다.
2. 기존 `SafeProjectBundle`로 immutable baseline을 만들고 별도 writable copy를 생성한다.
   실제 source root는 Worker cwd나 writable root가 아니다.
3. Codex는 빈 임시 HOME과 `auth.json`만 복사한 임시 `CODEX_HOME`을 사용한다. plugin, app,
   multi-agent, web과 command network를 끈다. Agent shell environment는 PATH·locale만
   allowlist하며 설치된 Codex와 같은 native sandbox launcher를 사용한다.
4. Codex는 writable copy 안에서만 읽기·수정·로컬 테스트를 수행한다. 모델의 완료 주장은
   증거로 간주하지 않는다.
5. host가 baseline과 결과를 다시 비교한다. 허용 밖 변경, 파일 삭제, 임의 숨김 파일,
   symlink, binary, 크기·patch·line 예산 초과와 빈 diff를 거부한다.
6. host가 Worker와 별도로 등록된 `python_unittest`를 network·HOME·사용자 filesystem이 없는
   `bubblewrap` 안에서 실행하고, 변경 Python 파일의 syntax와 중복 top-level 정의를 검사한다.
   검증 sandbox가 없으면 untrusted test를 host 권한으로 실행하지 않고 실패한다.
7. 모든 gate가 통과해도 결과는 hash가 붙은 patch와 `awaiting_adoption` 상태일 뿐이다.
   이 adapter에는 source 적용, commit, Capability 등록 기능이 없다.
8. 기존 CLI·Web·Voice의 read-only Codex V0는 변경하지 않는다.

## Verification

오프라인 테스트는 승인·경로·변경 범위, 원본 byte 불변, diff hash, 삭제·숨김·symlink·
binary·크기·line 예산, 빈 변경, 독립 test sandbox 부재·실패와 중복 정의 차단을 확인한다. 기존 typed
read-only gateway 회귀도 함께 유지한다.

실제 gpt-5.4 합성 probe에서는 `return left - right`를 `return left + right`로 한 줄
교체했다. Codex 내부 unittest 2개, host 독립 unittest 2개와 Python 구조 검사가 통과했고,
source 원본은 그대로였다. 최종 실행은 약 27.9초였다.

초기 live 반복에서 sandbox launcher가 PATH에 없을 때 모델이 기존 코드를 읽지 못한 채 중복
함수를 덧붙였고 기존 행동 테스트만 통과하는 실패가 있었다. 이 결과를 성공으로 숨기지 않고,
설치 버전과 일치하는 launcher 선택과 중복 정의 gate를 추가하는 근거로 사용했다.

재현 명령:

```bash
PYTHONPATH=src /home/hanelso/.local/bin/python3.11 \
  experiments/codex_isolated_work_probe.py
```

## Consequences

Worker는 이제 실제 코드를 수정하고 테스트할 수 있지만 그 결과가 운영 source에 자동 반영되지는
않는다. 구현 승인과 채택 승인이 기술적으로 분리됐고, 실패한 결과는 채택 가능 상태가 되지
않는다.

현재 검증 profile은 작은 Python `unittest` 프로젝트에 한정된다. proposal 영속화, 근거 기반
Winter 설명과 exact hash 채택 결정 기록은 [ADR-0019](0019-persist-and-explain-change-proposals.md)에서
후속 구현했다. Winter 실제 repository의 pytest/Ruff/mypy/Docker profile, stale baseline
재확인, 대화형 route, patch 적용과 rollback은 다음 issue다. 또한 단일 synthetic 성공은 악성 project instruction,
장시간·중단 복구, CPU·memory resource limit이나 외부 container 수준의 credential 격리를
증명하지 않는다.
