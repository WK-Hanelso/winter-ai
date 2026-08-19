# ADR-0019 — Persist Exact Change Proposals and Let Winter Explain the Evidence

- 상태: Accepted, Shadow only
- 날짜: 2026-08-18

## Context

ADR-0018은 Codex가 폐기 가능한 복사본만 수정하고 Winter host가 독립 검증한 뒤 원본을
건드리지 않는 `awaiting_adoption` patch를 만드는 경계를 확정했다. 그러나 patch가 process
종료와 함께 사라지고, 천우가 raw diff나 Worker의 자기 보고를 직접 해석해야 한다면 실사용
기능이 아니다. 겨울이가 Worker를 관리한다는 제품 원칙에도 맞지 않는다.

OpenAI 공식 [non-interactive 문서](https://learn.chatgpt.com/docs/non-interactive-mode)는
`codex exec`의 구조화된 결과와 JSONL event를 자동화 입력으로 사용할 수 있음을 설명한다.
하지만 구조화됐다는 사실은 내용이 참이라는 뜻이 아니다. Worker의 summary는 참고자료이고,
천우에게 전달할 최종 수정 설명은 승인된 목적, 실제 diff와 Winter host가 재실행한 검증에서
만든다.

## Decision

1. 격리 Worker 결과를 반환하기 전에 `SqliteChangeProposalRepository`에 저장한다. 저장
   대상은 구현 승인 ID와 목적, 정확한 patch와 SHA-256, 실제 변경 파일, 검증 결과,
   Winter 설명과 상태 이력이다.
2. `ChangeExplanation`은 다음을 분리한다.
   - 왜 고쳤는가: 천우가 승인한 objective
   - 실제 무엇이 달라졌는가: unified diff와 changed-file metadata
   - 무엇을 확인했는가: Worker 보고가 아니라 host verification 결과
   - 현재 검증의 한계: 실행한 verification profile 범위
   - Worker 참고사항: 신뢰하지 않은 별도 notes
   - Winter의 권고와 채택 질문
3. Worker summary는 Winter 설명에 복사하지 않는다. Worker가 배포·계정 권한처럼 실제로
   하지 않은 일을 주장해도 최종 설명에 들어가지 않는다.
4. 검증 실패 proposal은 `revise`만 권고하고 적용 질문을 만들지 않는다.
5. `adopt`, `revise`, `reject` 결정은 append-only event로 남긴다. `adopt`는 천우가 본
   patch의 정확한 SHA-256을 함께 제시해야 기록된다.
6. 채택 승인 기록은 source 적용 권한이 아니다. 이 단계에는 patch 적용, commit,
   Capability Registry 변경 기능을 두지 않는다.
7. 현 local semantic model은 Work Orchestration activation gate를 통과하지 못했으므로,
   설명의 사실 골격은 deterministic하게 만든다. 단일 line 교체는 before/after를 직접
   설명하고, 복수 line 변경은 파일별 추가·삭제 line 수까지만 단정한다.

## Verification

오프라인 테스트는 설명의 근거 분리, 실패 proposal 채택 차단, 다른 patch의 proposal ID
재사용 차단, patch hash가 다른 채택 승인 거부, 재작업 이유와 상태 이력, source 불변과
SQLite 재시작 후 동일 proposal 복원을 확인한다. 악의적인 Worker summary가 최종 설명에
섞이지 않는 것도 고정한다.

실제 gpt-5.4 합성 probe에서는 `return left - right`를 `return left + right`로 바꾼 exact
patch와 host `python_unittest`, `python_structure` 통과 범위를 겨울이 설명으로 만들었다.
process 종료 뒤 SQLite를 다시 열어 proposal, 설명과 hash가 동일함을 확인했고 source는
변하지 않았다.

재현 명령:

```bash
PYTHONPATH=src /home/hanelso/.local/bin/python3.11 \
  experiments/codex_isolated_work_probe.py
```

## Consequences

겨울이는 이제 Worker의 “완료했다”는 말을 전달하는 대신 실제 변경과 검증 근거를 자기
판단 형식으로 설명할 수 있고, 천우의 `적용해` 또는 `다시 고쳐줘` 결정을 정확한 proposal에
묶어 보존할 수 있다. 재시작해도 확인 대상이 바뀌지 않는다.

일반 대화에서 이 경로를 시작하거나 결정을 해석하지 않으며 복수 line 변경의 의미 설명은
제한적이다. 대상 stale 검사, 적용과 rollback은
[ADR-0020](0020-fail-closed-change-adoption-and-rollback.md)에서 후속 구현했다. project 전체
baseline, process crash 복구, 실제 Winter용 pytest/Ruff/mypy 검증 profile과 대화 route는
여전히 남아 있다.
