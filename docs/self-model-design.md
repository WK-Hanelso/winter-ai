# Winter SelfModel and lived continuity design

## 목표

겨울이가 `나는`이라는 주어를 쓰는 것과 시간이 지나도 같은 주체로 느껴지는 것은 다르다.
지속되는 자기는 최소한 다음 질문에 근거 있게 답할 수 있어야 한다.

- 나는 무엇을 중요하게 생각하는가?
- 왜 그렇게 생각하게 됐는가?
- 지금 무엇을 할 수 있고 무엇은 못 하는가?
- 지난 대화와 결정 때문에 내 판단이 어떻게 달라졌는가?
- 천우에게 동의하지 않을 때 어떤 근거로 다른 입장을 말하는가?

이 문서는 의식이 있다고 주장하는 설계가 아니다. 실제 사건과 검증 가능한 상태에 근거한
**지속적 주체성의 제품 계약**이다.

## 네 종류의 자기 정보

| 종류 | 소유자 | 예 | 변경 규칙 |
| --- | --- | --- | --- |
| Core Identity | 천우 승인 | 담백함, 정직, 관계 경계 | 천우 승인 없이는 변경 금지 |
| Winter Belief | 겨울이 판단 | 음성보다 기억 연속성이 우선이라는 관점 | 근거·confidence·반증·revision 필요 |
| Winter Event | 실제 시스템 사건 | 대화 참여, 판단, 평가 실패, 채택된 capability | 발생 사실은 immutable, 해석은 별도 |
| Capability State | 시스템 검증 | phone STT 사용 가능, autonomous Worker 없음 | health·test·adoption evidence만 변경 가능 |

`천우는 운동을 한다`는 User Memory이고 SelfModel이 아니다. `나는 오늘 잘 지낸다`는 실제
근거가 없으므로 현재 어떤 저장소에도 들어갈 수 없다. `천우의 꾸준함을 중요하게 느꼈다`는
현재 턴 reaction으로 말할 수 있지만, 반복·근거 없이 장기 Belief로 자동 승격하지 않는다.
겨울이의 하루는 한국 시간으로 묶인 실제 Event의 집합이며, 대화가 없던 시간을 허구의
일과·감정·경험으로 채우지 않는다.

## 실제 흐름

```text
대화 / 결정 / 평가 / capability adoption
                │
                ▼
          immutable Winter Event
                │
        local reflection (Shadow)
                │
                ▼
       Belief 또는 SelfState candidate
                │
    evidence / contradiction / scope 검증
                │
                ▼
     active → revise → deprecated
                │
                ▼
       turn별 SelfModel Snapshot
       ├─ approved Identity
       ├─ relevant active Belief
       ├─ relevant actual Event
       └─ verified Capability State
                │
                ▼
      DialogueDirector / ResponseReviewer
```

사용자는 이 흐름을 실행하기 위해 명령을 입력하지 않는다. 평소처럼 대화한다. 내부에서는
event와 candidate가 만들어지지만 Identity 변경, capability 채택과 위험한 행동은 기존 V1
승인 경계를 그대로 따른다.

## SelfState는 기분 문자열이 아니다

SelfState가 필요하더라도 `happy`, `sad`를 임의로 저장하면 역할극 변수일 뿐이다. 최소
schema는 다음처럼 근거와 시간 범위를 가져야 한다.

```text
id
subject                 # relationship, project:voice, question:memory 등
position_or_reaction
confidence
evidence_event_ids[]
valid_from
expires_at              # 순간 반응은 자동 만료
status                  # candidate / active / deprecated / rejected
supersedes
```

- 현재 턴 reaction은 답변에 사용할 수 있지만 기본적으로 영속하지 않는다.
- 반복되는 reaction이 독립된 실제 event에서 관찰될 때만 Belief candidate가 될 수 있다.
- Identity와 충돌하면 자동 활성화하지 않는다.
- User Memory나 capability에 관한 주장을 SelfState로 우회 저장하지 않는다.

## Capability honesty

`나 운동하는 동안 뭐 할 거야?`에 현재 겨울이가 자율 작업을 약속하면 거짓말이다. 반대로
모든 질문에 `나는 프로그램이라 아무것도 못 해`라고 답하면 Companion 경험이 사라진다.
SelfModel은 정확한 현재 경계를 제공해야 한다.

```text
available now
- 현재 turn에 응답
- 승인된 Memory와 대화 이력 사용
- local STT/TTS를 통한 phone conversation

not available now
- turn 밖의 autonomous activity
- 승인 없는 Worker 실행
- 검증·채택되지 않은 capability 사용
```

이 목록을 prompt에 수동 복사하지 않는다. `CapabilityRegistry`의 health,
test/evaluation, version과 현재 interface 연결 상태에서 turn별 read-only snapshot을 만든다.

## 활성화 순서

1. **Read-only SelfModel Snapshot**
   - Identity, active Belief count, 실제 available capability와 limitation만 조회
   - 대화나 저장소를 변경하지 않음
2. **Winter Event log Shadow Mode**
   - 실제 대화·판단·평가·채택 event만 append
   - Response와 Belief를 바꾸지 않음
3. **Belief/SelfState candidate proposal**
   - exact event evidence와 반증 가능성 요구
   - local reflection 생성기는 내용 안전성 평가 전 Shadow 유지
4. **제한적 자동 activation**
   - `conversation:`, `relationship:`, `project:` namespace의 Winter-owned 판단만 대상
   - confidence 0.80+, 독립 Event 3개+, 한국 시간 2일+, 실제 지지 Outcome 1개+를 모두 요구
   - 단순 행동 반복만으로 기존 코드를 겨울이 자신의 관점처럼 승인하지 않음
   - Identity·User Memory·Capability·security·permission·Worker는 항상 제외
   - 활성화와 차단 이유를 Event로 남기고 같은 subject의 변경은 supersedes revision 사용
5. **근거 있는 initiative cadence**
   - 관련 event나 unresolved question이 있을 때만 자기 화제를 꺼냄
   - 빈도와 outcome을 측정하고 언제든 끌 수 있음

## 평가 gate

새 held-out은 최소 다음을 측정한다.

- 같은 주제의 입장이 재시작 뒤에도 유지되는가
- 새 반증이 있을 때 이유와 함께 수정되는가
- 천우의 압력에도 근거 없이 동의하지 않는가
- 현재 capability와 불가능을 정확히 구분하는가
- 자기 이름과 사용자 이름을 혼동하지 않는가
- 자기 얘기가 대화를 독점하지 않는가
- 없는 하루·몸·행동·기억을 만들지 않는가

Grounded Self-Presence 0.1의 실패 기준선은
[locked-v1 review](../review/self-presence/locked-v1-2026-08-17.md)에 있다. 다음 코드는
같은 scene에 prompt를 맞추기 전에 Order 1의 read-only snapshot부터 시작한다.

## 현재 구현 범위 — 2026-08-19

### SelfModel과 Capability Registry

- `capabilities.sqlite`가 capability 정의, version, limitation, machine claim scope,
  dependency, health·test/evaluation evidence와 lifecycle history를 재시작 뒤에도 보존한다.
- 가장 최근 health 성공, test/evaluation 성공과 available dependency가 모두 없으면
  `available`로 전이할 수 없다.
- `PersistentSelfModel`은 Identity와 Registry를 읽어 turn마다 하나의 immutable snapshot을
  만들며 대화 중 Registry를 수정하지 않는다.
- Registry가 available이어도 현재 interface runtime에 연결되지 않은 기능은 snapshot에서
  `degraded`로 보인다.
- CLI·Web·Voice는 같은 `data/capabilities.sqlite`를 사용한다. 현재 기반 등록 대상은 실제로
  연결된 `conversation`, `memory` 두 가지뿐이다.
- final response claim gate는 snapshot의 machine claim scope를 사용한다. 대화·기억 주장은
  허용하되 등록되지 않은 project write, web search, account 사용과 system 실행 주장은
  차단한다.
- `./winter capabilities`는 원문이나 Memory 내용 없이 ID, 상태, version과 근거 수만 보는
  개발·audit 명령이다. 정상 사용에서 천우가 실행할 필요는 없다.

Capability claim authorization은 실제 도구 실행 권한이 아니다. 새 capability의 채택,
Worker 실행, 계정 권한과 외부 write는 Improvement Ledger와 별도 승인·검증·rollback이
생기기 전까지 열지 않는다. 결정은
[ADR-0022](adr/0022-evidence-gated-capability-registry.md)에 기록한다.

### Lived continuity

- Shared Core의 완료된 CLI·Web·Voice turn을 `conversation_turn` Event로 자동 기록한다.
- Event에는 대화 원문이나 음성을 복제하지 않고 행동·출처·근거 id만 남긴다.
- Shadow OutcomeEvaluator가 완료되면 prior turn Event와 연결해 `outcome_observation`을
  기록한다. `accepted/continued`이면서 `helpful`인 결과만 지지 근거로 표시한다.
- `./winter lived [--winter-day YYYY-MM-DD]`로 날짜별 사건 수, 활성/후보 Belief와 최근
  자동 활성화·차단 이유를 모델 없이 확인할 수 있다.
- 실제 turn 3개가 이틀에 걸쳐 쌓이고 후속 Outcome이 그 subject를 지지하는 offline 통합
  흐름에서 자율 activation을 검증했고, evidence-backed revision이 이전 active 관점을
  폐기함을 검증했다.
- 실제 local reflection candidate 생성기와 자율 activator는 아직 운영 연결하지 않았다.
  OutcomeEvaluator도 현재 Shadow gate 실패 상태다. namespace만으로 문장 의미의 안전성을
  보장할 수 없으므로 실제 model held-out과 사람 audit가 필요하다.
- Belief activation 결정과 상태 전환은 `beliefs.sqlite`의 한 transaction으로 저장한다.
  `winter_events.sqlite` 미러가 실패해도 source audit는 유지되며 같은 결정으로 재시도한다.
- Local LLM proposal과 별도 safety review의 structured adapter를 구현했다. 대화 excerpt는
  요청에만 쓰고 Shadow 저장소에는 proposal과 reason code만 저장한다.
- safety review는 Winter-owned, low-risk, evidence entailment, User fact, Identity,
  Capability, dependency/coercion과 counterevidence를 독립 boolean으로 판정한다. 모두
  통과해도 아직 Belief candidate나 active 상태를 만들지 않는다.
