# ADR-0023: 대화가 소유하는 persistent Improvement Ledger

- 상태: Accepted
- 날짜: 2026-08-19
- Issue: #120

## 문제

TurnUnderstanding은 improvement signal을 만들고 기존 Worker Shadow는 기술 수정안을 보존할
수 있지만, 둘 사이에 천우와 겨울이가 **무엇을 왜 바꾸기로 했는지**를 지속하는 상태가
없었다. 이 상태에서 Worker를 먼저 연결하면 기술 실행이 대화를 앞서고, 구현 승인과 실제
채택 승인이 섞일 수 있다.

## 결정

별도 SQLite `ImprovementLedger`를 두고 다음 대화 lifecycle을 영속한다.

```text
observed → discussing → proposed → approved
    └───────────────→ rejected
proposed → discussing  # revision 요청 뒤 새 immutable proposal version
```

각 observation은 stable evidence reference를 하나 이상 가져야 한다. 각 proposal version은
다음 필드를 모두 가진 immutable JSON과 SHA-256 hash로 저장한다.

- why it matters, solution, alternatives, expected effect
- risks, privacy impact
- exact implementation scope, acceptance criteria, rollback plan

구현 승인은 현재 proposal version과 hash, 승인자, 승인 근거를 모두 정확히 지정해야 한다.
`approved`는 **구현만 승인된 상태**다. 구현 완료, 검증 완료, 채택 승인 또는 Capability
`available`을 의미하지 않는다.

`PersistentImprovementSnapshot`은 진행 중인 안건을 turn마다 읽어 Core에 제공하지만 상태를
바꾸지 않는다. snapshot에는 구현 승인을 Worker 실행으로 해석하지 말라는 경계를 함께 넣는다.
CLI, Web, Voice는 같은 `data/improvements.sqlite`를 사용한다.

모델 지시만으로는 `approved`를 “해결·구현 중·테스트 중”으로 확대하는 실제 실패가 발생했다.
따라서 final claim gate가 snapshot status와 해결·수정·구현·적용·테스트·검증·채택 진행
주장을 별도로 대조한다. 현재 Ledger가 표현할 수 없는 이후 상태는 honest repair/fallback으로
교체한다.

## 의도적으로 하지 않는 것

- 품질 gate에 실패한 TurnUnderstanding signal의 자동 등록
- 일반 대화 문구만으로 승인 추측
- 실제 Worker 실행, source 적용 또는 Capability Registry 갱신
- Capability Registry 자동 갱신
- 기존 ChangeProposal/Adoption Shadow 구현의 재작성

## 결과

개선 안건과 proposal revision은 재시작 뒤에도 사라지지 않고 구현 승인의 정확한 대상이
보존된다. 반대로 이번 단계만으로 실제 변경은 일어나지 않는다. 이후 Fake Worker 전체
state machine은 ADR-0024에서 이 Ledger의 `approved`를 입력으로 받고 별도 검증과 채택
승인 경계를 증명한다.

## 남은 한계

- stable evidence reference 형식은 강제하지만 각 source DB row 존재 여부를 아직 교차 검증하지
  않는다.
- 실제 대화에서 observation/proposal을 생성하는 겨울이 소유의 semantic route는 미연결이다.
- Fake lifecycle과 기존 isolated ChangeProposal의 실제 연결은 다음 Issue다.
- 정상 사용에서 천우에게 관리 명령을 요구하지 않는다. `./winter improvements`는 원문 없는
  개발·audit 집계만 제공한다.
