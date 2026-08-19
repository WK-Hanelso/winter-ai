# Foundation Recovery Baseline — 2026-08-19

## 목적

감사 이전부터 한 worktree에 섞여 있던 사용자·Claude·Codex 변경을 삭제하거나 성공한 기능으로
재해석하지 않고, 이후 변경과 구분할 수 있는 local recovery point로 보존한다.

## 기준선

- GitHub Issue: #113
- branch: `chore/113-foundation-baseline`
- parent: `de16bba1885c51e14403b49297292fbf55ca184c`
- preservation snapshot: `cb4bb4bf289fae82f5a9e96d504c15ecefd23f8a`
- snapshot 범위: modified 44개, non-ignored untracked 103개, 총 147개 파일
- Git 후보 크기: 약 2.47 MB
- remote push: 하지 않음

이 커밋은 여러 기능의 **보존점**이지 각 기능의 acceptance, activation 또는 V1 완료를
의미하지 않는다. 기능별 실제 상태는 `project-state.toml`과 Maturity Ledger를 따른다.

## Commit 전 검사

- Git 후보 파일은 모두 text였다.
- `.env`, DB, 대화 데이터, model, checkpoint, review, generated audio, Human Reference
  media는 `.gitignore` 경계 밖으로 나오지 않았다.
- 대표 API key, GitHub token, Slack token, private key와 plaintext password 패턴은 staged
  content에서 발견되지 않았다.
- Git 대상에 audio/video/SQLite 파일과 실제 source URL이 없었다.
- `git diff --cached --check`를 통과했다.

## 회귀 근거

snapshot 직전에 같은 staged tree에서 검증했다.

```text
./winter project-check: PASS
pytest: 681 passed
Ruff: all checks passed
mypy src experiments: 161 source files, no issues
```

## 복구와 한계

- snapshot 전체는 branch 또는 commit SHA로 다시 확인할 수 있다.
- 기존 기능을 작은 Issue/PR로 재구성한 것은 아니다. 과거 변경의 provenance는 원래 commit과
  문서·평가 기록에 의존한다.
- secret 검사는 대표 패턴 기반이며 모든 형태의 민감정보 부재를 수학적으로 보장하지 않는다.
- remote에 올리기 전에는 같은 privacy 검사와 전체 diff review를 다시 수행한다.
- 이후 작업은 이 clean baseline 위에서 active Issue의 diff만 만들어야 한다.
