# ADR-0026: source 적용 전 project baseline과 crash recovery

- 상태: Accepted, Shadow only
- 날짜: 2026-08-20
- Issue: #123

## 문제

기존 `ChangeAdoptionService`는 수정 대상 파일의 before hash만 확인하고 여러 파일 적용 중 일반
예외를 복원했다. 하지만 수정안 생성 뒤 다른 source 문맥이 달라지는 경우, 적용 직후 전체
repository 회귀, process kill이나 전원 중단으로 남은 `applying`·`rolling_back` 상태는 다루지
못했다.

## 결정

### Privacy-safe project baseline

Worker에게 허용하는 것과 같은 source 선택 정책으로 path, size와 SHA-256만 가진 정렬 manifest를
만든다. 다음은 baseline에서 제외한다.

- `data`, 대화 DB, 모델, checkpoint, 음성, Human Reference와 review 자료
- `.git`, `.codex`, `.claude`, `.agents`, cache와 hidden path
- `.env`와 credential
- 허용 크기를 넘는 binary 또는 비검토 파일

`IsolatedWorkResult`는 수정 전 baseline과 manifest hash를 함께 보존한다. 실제 apply 직전에
현재 manifest가 다르면, 변경이 patch 대상 밖이어도 proposal을 `stale`로 만들고 어떤 patch
bytes도 쓰지 않는다. baseline이 없는 과거 proposal은 자동 보정하지 않고 적용을 거부한다.

### Post-apply full repository verification

apply에는 명시적인 `RepositoryVerifier`가 필수다. `CommandRepositoryVerifier`는 등록된 argv
목록만 `shell=False` 방식으로 순서대로 실행하고 첫 실패에서 중단한다. 모든 command가
`status=pass`, `exit_code=0`이어야 `applied`가 된다.

실패하면 모든 target을 snapshot의 before bytes로 복원하고 `apply_failed`를 기록한다. audit
reason에는 command, status, exit code와 output hash만 남기며 출력 원문을 장부에 복제하지
않는다.

### Process crash reconciliation

apply 시작 전에 저장된 before content와 before/after hash를 이용해 재시작 시 각 target을
세 상태로 분류한다.

```text
before  저장된 적용 전 bytes와 일치
after   저장된 적용 후 bytes와 일치
unknown 둘 다 아님
```

`applying` 복구:

- all-after: full verifier 통과 뒤 `applied`, 실패하면 before로 복원하고 `apply_failed`
- all-before 또는 before/after 혼합: 모두 before로 복원하고 `apply_failed`
- unknown 포함: 자동 write 없이 상태를 유지하고 수동 복구 요구

`rolling_back` 복구:

- before/after로만 구성된 어떤 조합도 모두 before로 복원하고 `rolled_back`
- unknown 포함: 자동 write 없이 수동 복구 요구
- 복원 실패 시 가능한 경우 all-after로 되돌리고 `rollback_failed`

이미 종료된 상태의 반복 `recover`는 파일이나 event를 다시 바꾸지 않는다.

## 의도적으로 하지 않는 것

- 운영 Winter source에 실제 proposal 적용
- conversation 또는 lifecycle adoption과 source apply 연결
- commit, push, deploy, service restart
- Capability Registry 전이
- hostile filesystem writer에 대한 OS lock
- 바뀐 baseline의 자동 승인

## 결과와 다음 gate

임시 fixture에서 target stale뿐 아니라 project 문맥 drift, post-apply 실패와 crash 중간 상태를
fail-closed로 복원할 수 있다. 다음 단계는 별도 Issue에서 lifecycle adoption decision과 이
source-adoption gate를 exact proposal/patch/baseline identity로 연결하고 synthetic capability
하나의 전체 cycle을 수행하는 것이다.
