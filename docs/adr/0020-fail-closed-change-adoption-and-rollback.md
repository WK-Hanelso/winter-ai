# ADR-0020 — Apply Only an Exact Target Baseline and Keep a Local Rollback Snapshot

- 상태: Accepted, Shadow only
- 날짜: 2026-08-18

## Context

ADR-0019까지 Winter는 검증된 patch를 설명·영속화하고 천우의 별도 채택 결정을 exact patch
hash에 묶어 기록할 수 있었다. 그러나 `adoption_approved`는 의도적으로 source를 바꾸지
않았다. 실용적인 Level 3 cycle에는 승인 뒤 실제 적용과 실패 시 복원이 필요하다.

격리 수정안을 만든 뒤 사람이 같은 파일을 고쳤을 수 있고, 여러 파일 적용 중 일부만 바뀔
수도 있다. rollback 역시 적용 뒤 생긴 새 편집을 지워서는 안 된다.

## Decision

1. `ChangeAdoptionService`는 `adoption_approved` proposal과 천우가 확인한 exact patch
   SHA-256이 모두 일치할 때만 시작한다.
2. 각 수정 대상의 현재 bytes를 proposal의 `before_sha256`과 비교한다. 수정 파일이
   달라졌거나 추가 예정 파일이 이미 있으면 `stale`로 기록하고 아무것도 덮어쓰지 않는다.
3. unified diff를 host가 직접 해석하고 재구성한 bytes가 Worker 결과의 `after_sha256`과
   일치해야 한다. 임의 shell `patch` 명령이나 Worker summary를 적용 근거로 쓰지 않는다.
4. 적용 전에 수정 대상별 원문 bytes와 before/after hash를 local SQLite BLOB으로 저장하고
   `applying` event를 남긴다. 이 DB는 private runtime data이며 Git에 넣지 않는다.
5. 각 파일은 같은 directory의 임시 파일에 쓰고 `fsync`한 뒤 `os.replace`한다. 여러 파일
   중 하나가 실패하면 이미 처리한 파일을 포함해 모든 대상의 적용 전 bytes를 복원하고
   `apply_failed`를 기록한다.
6. 성공 뒤 실제 bytes를 다시 대조하고 `applied`를 기록한다. 적용 후 파일이 그대로일 때만
   rollback을 시작한다. 이후 편집이 발견되면 자동으로 덮어쓰지 않는다.
7. rollback은 수정 파일의 원문을 복원하고 proposal이 추가한 파일은 제거한다. 실패하면
   가능한 범위에서 적용 상태를 다시 복원하고 `rollback_failed`를 기록한다.
8. 이 service는 commit, push, 배포, Capability Registry 갱신이나 대화 route를 수행하지
   않는다.

## Verification

오프라인 테스트는 exact baseline 적용, stale 차단, 두 파일 중 두 번째 교체 실패 때 첫 번째
파일까지 복원, SQLite 재시작 뒤 수정·추가 파일 rollback, 적용 후 사람 편집 보호를 확인한다.

실제 gpt-5.4 합성 probe는 Codex 구현, Winter 설명, source 불변, 채택 승인, 실제 한 줄 적용,
rollback과 원본 byte 복원을 한 process에서 완주했다.

재현 명령:

```bash
PYTHONPATH=src /home/hanelso/.local/bin/python3.11 \
  experiments/codex_isolated_work_probe.py
```

## Consequences

승인된 수정안은 이제 실제 파일에 적용되고 재시작 뒤에도 rollback할 수 있다. 천우가 raw
patch를 수동으로 적용하거나 백업 파일을 관리할 필요가 없다. 오래된 대상과 적용 후 편집은
fail-closed한다.

현재 stale 검사는 수정 대상 파일의 exact bytes에 한정되며 project 전체 baseline 변화는
판정하지 않는다. 격리 단계의 검증 뒤 적용 직후 실제 Winter repository용 pytest/Ruff/mypy를
다시 실행하는 profile도 아직 없다. 일반 실행 오류는 전 파일 복원하지만 process kill이나
전원 차단 뒤 `applying`/`rolling_back` 상태를 자동 복구하지 않는다. hostile concurrent
filesystem 변경과 directory `fsync`도 아직 다루지 않는다. 이 항목들과 대화 연결이 다음
activation gate다.
