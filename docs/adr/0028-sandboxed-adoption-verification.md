# ADR-0028: source 채택 검증을 host 권한에서 분리한다

- 상태: Accepted, Shadow only
- 날짜: 2026-08-20
- Issue: #125

## 문제

#124는 exact 승인 뒤 source 적용, 전체 검증, Capability 등록과 Improvement 완료를 하나의
cycle로 묶었다. 그러나 `CommandRepositoryVerifier`는 고정 argv를 shell 없이 실행할 뿐,
적용된 source directory를 host 권한으로 열었다. 검증 대상 test나 repository 파일이
악의적이거나 손상됐다면 검증 과정에서 source·HOME·credential·network를 건드릴 수 있었다.
모델의 capability label이나 prompt 지시는 이 권한을 강제하지 못한다.

## 결정

isolated implementation Worker와 source-adoption verifier가 하나의
`BubblewrapVerificationSandbox`를 공유한다.

- 실제 project source는 `/workspace`에 read-only bind한다.
- writable filesystem은 private tmpfs `/tmp`만 제공한다.
- `--unshare-all`로 network와 나머지 namespace를 분리한다.
- user HOME은 bind하지 않고 `/nonexistent`로 설정한다.
- 부모 process가 verifier에 넘기는 environment는 `LANG`, 최소 `PATH`, `PYTHONUTF8`뿐이다.
- 명령은 코드에 등록된 argv tuple만 사용하고 shell을 호출하지 않는다.
- timeout, launch failure 또는 bubblewrap 부재는 실패다. host 직접 실행으로 fallback하지 않는다.
- verifier 전후 privacy-safe project baseline이 달라지면 채택을 실패시키고 적용 대상 bytes를
  적용 전 상태로 복원한다.

Python runtime이 `/usr` 또는 `/bin` 밖에 있을 때는 executable이 속한 runtime tree만
`/runtime`에 read-only bind한다. host 전체 PATH, HOME 또는 credential directory는 전달하지
않는다.

## 검증

### deterministic offline 회귀

- read-only workspace, private `/tmp`, namespace 분리와 최소 environment command 확인
- fixed argv가 project text나 shell 문자열로 변하지 않음을 확인
- timeout과 sandbox 부재가 host retry 없이 한 번의 실패로 끝남을 확인
- 정상 #124 exact cycle이 sandboxed adoption verifier를 거쳐 `adopted`까지 감을 확인
- verifier가 적용 source를 변조했다고 가정한 방어 테스트에서 변조를 감지하고 before bytes로
  복원하며 `apply_failed`가 됨을 확인

### 실제 Linux bubblewrap 공격 probe

`experiments/adoption_verifier_attack_probe.py`를 host의 실제 bubblewrap 0.4.0으로 실행했다.
hostile fixture가 다음 동작을 시도했고 모두 차단됐다.

- 적용 source 재작성과 새 source 파일 생성
- workspace symlink를 통한 host secret 읽기
- HOME·AWS·Codex·OpenAI credential 환경 접근
- host 임시 marker 생성
- host loopback listener와 외부 IPv4 socket 연결
- timeout 뒤 background child 잔존

private `/tmp` 쓰기만 성공했으며, 종료 뒤 source와 host secret은 동일하고 marker와 background
child는 없었다. probe 결과는 `status=pass`, `network=loopback-and-external-blocked`,
`timeout=blocked`, `background_child=gone`, `source=unchanged`였다. 전체 offline 회귀는
792개, Ruff 전체와 `mypy src experiments` 171개 source file을 최종 gate로 사용한다.

## 의도적으로 하지 않는 것

- 운영 Winter repository 또는 사용자 대화 route 연결
- 실제 Codex write Worker 활성화
- Capability runtime 실행 등록
- 계정 credential을 모델이나 sandbox에 전달
- commit, push, deploy 또는 service restart
- CPU·memory·process 수 resource limit
- hostile concurrent host writer에 대한 project-wide lock
- Linux bubblewrap 외 platform 지원

## 결과와 다음 gate

채택 뒤 실행되는 repository 검증은 더 이상 Winter host 권한으로 조용히 실행되지 않는다.
격리 경계가 없으면 capability를 채택할 수 없다. 이로써 #124 synthetic cycle의 가장 큰 실행
권한 누수를 닫았지만 write capability가 운영 가능한 것은 아니다. 다음 단계는 천우가 고른
작은 실제 capability 하나를 별도 승인 아래 이 cycle에 연결하고, 실제 repository 검증·재평가·
rollback 및 concurrent change를 사람 검토로 관찰하는 것이다.
