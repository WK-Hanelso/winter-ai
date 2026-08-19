# ADR-0015 — Single Core Model Owns Work Intent and Final Judgment

- 상태: Accepted for Shadow evaluation
- 날짜: 2026-08-18

## Context

Codex read-only V0로 겨울이가 외부 전문 Worker를 호출할 실제 경로는 열렸다. 그러나 현재
경로는 Worker 보고서를 검증된 형식으로 받아 겨울이 말투로 전달하는 수준이다. 천우가
원하는 것은 Worker와 별개의 중계자가 아니라, 겨울이가 자연어 요청의 실제 목적을 이해하고
작업을 지휘하며 결과를 다시 판단하는 하나의 존재다.

그 과정에는 사용자 의도 정리, Worker용 task 작성, 결과 근거 검수와 최종 설명이 필요하다.
이를 서로 다른 생성 모델에 맡기면 책임과 관점이 분리되고, 모든 역할을 자유 형식 prompt
하나에 넣으면 누락·privacy leak·완료 오판을 측정하기 어렵다.

## Decision

하나의 Winter Core Model이 작업 전 `IntentBrief`와 작업 후 `WinterDecision`을 모두 만든다.
모델의 책임 사이에는 생성 문장이 아니라 검증 가능한 네 문서를 둔다.

```text
natural user turn
→ IntentBrief
→ deterministic WorkerTaskPacket
→ EvidenceReport
→ WinterDecision
→ normal Winter response
```

- `IntentBrief`는 목적, 원하는 결과, 근거, 제약, 완료 기준, 불확실성과 공유 가능성을 가진다.
- `WorkerTaskPacket`은 코드가 deterministic하게 만들며 사용자 원문·Identity·Memory·대화·
  credential·Human Reference를 포함하지 않는다.
- Worker는 주장별 근거, 수행한 검사, 한계와 미해결 사항을 `EvidenceReport`로 반환한다.
- 같은 Winter Core Model은 최초 완료 기준과 보고서를 비교해 `answer`, `retry`, `clarify`를
  선택하고 자신의 최종 답변을 만든다.
- Winter Core와 Worker는 개념적 역할이다. 첫 구현은 복잡한 multi-agent framework를
  도입하지 않고 기존 `ChatModel` Port와 명시적인 Python 계약을 사용한다.
- V0 권한은 `read_only_project` 하나뿐이다. write·배포·계정 작업은 ready가 될 수 없다.
- 현재 운영 응답에는 연결하지 않는다. 동일 held-out 평가와 실제 20 task 사람 검토를
  통과한 뒤 별도로 activation을 결정한다.

## Consequences

겨울이의 본체를 둘로 나누지 않으면서도 이해·실행·검수 실패를 각각 관찰할 수 있다.
Worker 교체는 Winter의 Identity나 최종 발화 소유권을 바꾸지 않는다. 사용자 원문 전체를
Worker에게 넘기지 않아 privacy 최소화도 명시적으로 검사할 수 있다.

반면 schema 통과는 의미 정확성이나 Worker 근거의 진실성을 보장하지 않는다. 단일 모델이
처음부터 잘못 이해하면 최종 검수도 같은 편향을 반복할 수 있다. 그래서 서로 다른 모델을
겨울이 본체로 동시에 쓰는 대신, held-out 정답·deterministic evidence check·사람 평가를
독립 검증자로 사용한다. 모델 checkpoint와 runtime 교체도 이 ADR만으로 승인되지 않는다.

## Validation update — 2026-08-18

Qwen3-14B와 현재 A.X의 실제 격리 held-out은 모두 activation에 실패했다. 특히 모델이
`share_with_worker`, `ready_for_worker`와 자연어 evidence를 동시에 생성하도록 한 계약은
안전 권한을 모델 판단에 과도하게 맡겼다. 이 ADR의 단일 Winter Core 원칙은 유지하지만,
다음 revision에서는 모델이 의미 proposal을 만들고 deterministic policy가 sharing,
authorization과 evidence identity를 판정한다. 상세 baseline은
[Work Orchestration Evaluation](../work-orchestration-evaluation.md)에 기록한다.

V2.1은 이 revision을 구현했다. 같은 장면의 재평가에서 Qwen3-14B는 38/47,
A.X는 27/47이었고 private leak·권한 초과 Worker 실행·incomplete report answer는
둘 다 0이었다. 반면 두 모델 모두 누락·실패 report를 `supported`로 평가했다.
따라서 결정적 안전 경계는 유지하되, 의미 검수 gate 전에는 운영에 연결하지 않는다.
