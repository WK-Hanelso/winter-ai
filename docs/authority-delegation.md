# Winter Authority Delegation

- 상태: read-only Codex 제한 운영, isolated implementation proposal Shadow 구현
- 기준일: 2026-08-17
- 목표: 겨울이를 천우 바로 아래의 위임 주체로 만들되, 한 번의 오류가 천우의 모든
  계정 장악으로 확대되지 않게 한다.

## 제품 원칙

겨울이는 장기적으로 천우가 연결한 모든 계정의 **목록, 역할, 사용할 수 있는 기능,
권한 범위와 현재 연결 상태**를 알아야 한다. 하지만 계정의 비밀번호, recovery code,
session cookie, API secret 원문을 Identity·Memory·대화 prompt에 저장하거나 Worker에게
넘기지 않는다.

```text
천우 (root authority)
  └─ 겨울이 (delegated principal)
       ├─ account/capability registry: 무엇을 어디까지 할 수 있는지
       ├─ OS keyring / connector: 실제 credential 보관과 단기 token 발급
       ├─ policy engine: 이번 행동의 허용·승인 단계 결정
       ├─ Worker: 승인된 최소 자료와 작업만 수행
       └─ audit: 누가, 무엇을, 왜, 어떤 결과로 실행했는지
```

“모든 계정을 안다”와 “모든 비밀 원문을 모델 문맥에 넣는다”는 같은 말이 아니다. 겨울이는
계정 handle과 capability를 알고, 실제 인증은 OS credential store나 각 서비스 connector가
필요할 때만 대신한다. Worker는 token 자체가 아니라 이미 제한된 작업 수단을 받는다.

## 권한 단계

| 단계 | 예시 | 기본 정책 |
| --- | --- | --- |
| read | 메일·문서·코드 검색, 상태 확인 | 연결 시 합의한 scope 안에서 실행 가능 |
| draft | 답장·일정·코드 변경안 작성 | 실행 가능, 아직 외부 상태는 바꾸지 않음 |
| reversible write | 라벨, 개인 메모, 되돌릴 수 있는 내부 변경 | 계정별 standing policy와 audit 필요 |
| consequential write | 메일 발송, 게시, 일정 초대, 배포, merge | 대상·내용·범위를 보여 준 뒤 승인 |
| destructive / authority | 삭제, 결제, 송금, 보안 설정, 비밀번호·권한 변경, 다른 주체에 재위임 | 매번 천우의 just-in-time 승인 |

승인은 “겨울이 전체에 모든 권한” 한 번으로 끝내지 않는다. 계정과 행동 종류별 standing
policy를 둘 수 있지만, 마지막 단계는 항상 그 행동의 구체적 대상과 효과를 보고 승인한다.
승인이 없으면 겨울이는 초안이나 계획까지만 만든다.

## 현재 구현: Codex Read-only 0.1

천우가 평소 문장 안에서 `코덱스로 ... 분석해줘`, `코드엑스한테 ... 검토해줘`처럼 Worker와
행동을 함께 명시한 턴만 OpenAI Codex에 위임한다.

```text
천우 발화
→ Shared CompanionCore가 explicit opt-in 확인
→ 임시 sanitized project bundle 생성
→ Codex ephemeral + read-only 실행
→ answer / evidence / limitations 구조 검증
→ 겨울이가 출처를 밝히고 같은 CLI·Web·Voice 응답으로 전달
→ conversation turn에 Worker 성공·실패 provenance 기록
→ 임시 bundle 삭제
```

bundle 허용 범위는 코드, 테스트, 설정, 공개 가능한 설계 문서다. 다음은 복사하지 않는다.

- `.env`, `.git`, `.claude`, `.codex`
- `data/`의 Identity, Conversation, Memory, Belief와 SQLite
- `review/`, Human Reference, 음성·영상·model/checkpoint
- 숨김 파일, symlink, binary와 크기 제한을 넘는 파일

일반 대화나 단순한 `Codex가 뭐야?` 언급은 외부로 보내지 않는다. Worker가 실패하면 로컬
LLM이 대신 수행한 것처럼 보이지 않고 실패 상태를 말한다. 현재 모델은 설치된 CLI와
호환되는 `gpt-5.4`로 고정하며 설정으로 교체할 수 있다.

## 읽기 전용 경로의 알려진 한계

- 현재는 read-only 분석만 가능하다. 코드 변경, 테스트 실행, commit과 배포 권한은 없다.
- sanitized bundle은 전달 대상을 줄이지만 Codex CLI 프로세스 자체의 host read sandbox를
  별도 OS container로 제한한 것은 아니다. prompt와 bundle 경계를 넘어가지 않았는지 실제
  1회 확인했지만, 이것은 강제 격리와 같지 않다.
- 자연어 route는 explicit Worker 이름과 작업 동사 기반 V0다. Local TurnUnderstanding 기반의
  제안·확인 route와 false-positive 실측이 필요하다.
- 한 번의 작은 live probe는 장시간 운영, rate limit, 비용·token budget, 중단·재시도와
  서비스 재시작 복구를 증명하지 않는다.

## Typed Project Gateway Shadow

새 Work Orchestration 경로를 위해 provider-independent read-only gateway를 구현했다.
Worker에는 임의 shell 대신 파일 목록, bounded text 검색과 bounded line 읽기만 제공한다.
경로 탈출·symlink·숨김·binary·network/write operation을 거부하고, snapshot hash,
stable evidence ID, allowed/denied audit와 EvidenceReport provenance를 오프라인 9개 테스트로
검증했다. 설계 결정은 [ADR-0016](adr/0016-typed-read-only-project-gateway.md)을 따른다.

Shadow Codex provider adapter는 부모 process에만 임시 `auth.json`과 API transport를 두고,
모델에는 shell/web 없이 세 MCP tool만 노출한다. MCP는 `env -i`로 인증·HOME·proxy를 받지
않으며, 빈 agent directory와 별도 sanitized bundle을 사용한다. 실제 synthetic 1회에서
목록·읽기 audit과 evidence catalog 검증까지 통과했다. 설계 결정은
[ADR-0017](adr/0017-codex-control-plane-typed-tools.md)을 따른다.

이 adapter도 아직 위의 사용자-facing Codex V0에는 연결하지 않았다. 합성 1건은 악성
project prompt, 20개 실제 작업, 장시간·중단 복구를 입증하지 않으며 Codex CLI binary 자체의
credential 접근을 막는 별도 OS 격리도 아니다.

## Isolated Implementation Proposal — Shadow

write-capable Worker의 첫 수직 단면은 실제 source가 아니라 `SafeProjectBundle`의 baseline과
별도 writable copy에서만 동작한다. 천우의 구현 승인을 나타내는
`ImplementationAuthorization`에는 정확한 수정 파일, 검증 profile과 line 예산이 들어간다.

Codex는 격리 copy 안에서 shell·수정·로컬 테스트를 사용할 수 있지만 plugin/app/web/command
network는 꺼진다. 실행 뒤에는 host가 모델 보고와 별개로 diff scope, 삭제·숨김·symlink·
binary·크기·line 예산을 검사하고 `python_unittest`를 network·HOME 없는 `bubblewrap`에서
다시 실행한 뒤 Python syntax/중복 정의를 검사한다. 검증 sandbox가 없으면 fail-closed한다.
모두 통과해도 source를 바꾸지 않고 `awaiting_adoption` patch만 반환한다.

gpt-5.4 synthetic live에서 한 줄 산술 버그를 최소 교체하고 내부/외부 unittest 2개와 구조
검사를 통과했으며 source가 그대로임을 확인했다. 결과는 이제 exact patch hash와 함께
SQLite에 보존되고, 겨울이는 승인 목적·실제 diff·host 검증 범위·Worker 참고사항을 분리해
설명한다. `adopt` 결정은 천우가 확인한 exact hash에만 기록되며 source를 적용하지 않는다.
재시작 뒤 같은 proposal과 설명이 복원되는 실제 probe도 통과했다.

채택된 proposal은 대상 파일의 exact before hash가 유지될 때만 실제 적용할 수 있다. 적용
전 bytes를 SQLite에 보존하고 파일별 임시 교체를 사용하며, 중간 오류는 전체 대상 복원을
시도한다. 적용 뒤 다른 편집이 없을 때만 수정 파일을 복원하고 추가 파일을 제거한다. 실제
Codex 합성 probe에서 적용과 rollback 뒤 원본 byte 복원을 완주했다.

이 경로는 아직 사용자-facing 대화, project 전체 stale 판정, process crash 자동복구와 실제
Winter pytest/Ruff/mypy에 연결하지 않았다. commit·push·배포 권한도 없다. 격리 결정은
[ADR-0018](adr/0018-isolated-codex-implementation-proposal.md), 영속화와 설명 근거는
[ADR-0019](adr/0019-persist-and-explain-change-proposals.md), 적용·rollback은
[ADR-0020](adr/0020-fail-closed-change-adoption-and-rollback.md)에 있다.

남은 전체 write 경로는 다음 순서를 유지한다.

```text
구체적 변경 제안
→ 천우의 구현 승인
→ private data가 없는 격리 copy — Shadow 구현
→ 승인된 path/command scope만 실행
→ Worker와 독립된 scope·unittest·Python 구조 검증 — V0 구현
→ 실제 diff·검증 범위·Worker notes를 분리한 Winter 설명 — Shadow 구현
→ proposal 영속화와 exact hash 기반 별도 채택 결정 — Shadow 구현
→ 대상 exact baseline 확인·적용·rollback — Shadow 구현
→ project 전체 stale·crash recovery·실제 repository 재검증
→ Capability Registry 갱신
```
