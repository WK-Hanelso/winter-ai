# TurnUnderstanding Shadow Mode

## 목적

`TurnUnderstanding`은 겨울이가 천우의 한 문장을 단일 label로 확정하지 않고, 관찰 가능한
원문과 여러 해석 가설을 분리해 축적하기 위한 V1의 첫 관찰 계층이다.

현재는 **Shadow Mode**다. 분석 결과는 답변 prompt, `DialogueDirector`, Memory,
Belief, Open Loop와 TTS에 주입되지 않는다. 먼저 실제 대화에서 해석 품질과 오판 유형을
측정한 뒤에만 다음 단계인 `OutcomeEvaluator`의 입력으로 사용할 수 있다.

```text
CLI / Web / Voice
        │
        ├─ CompanionCore → 기존 응답·Memory 경로
        │
        └─ raw turn → pending SQLite event
                            │
                  별도 CLI 분석 명령
                            │
                  local LLM → strict validation
                            │
                    completed / failed
```

## 저장 계약

세 인터페이스는 같은 `data/turn_understanding.sqlite`를 사용하고 각각 `cli`, `web`,
`voice` source를 남긴다. Core는 현재 사용자 원문과 이미 적용된 bounded conversation
context만 저장한다. Identity, system prompt, 전체 장기 기억을 분석 요청에 복제하지 않는다.

이벤트 상태는 다음과 같다.

- `pending`: 원문과 문맥이 먼저 영속화됐고 아직 분석하지 않음
- `completed`: 로컬 분석 결과가 모든 schema 검증을 통과함
- `failed`: 모델 접근·JSON·schema·evidence 검증 중 하나가 실패함

분석 결과에는 다음 필드가 모두 있어야 한다.

- literal meaning과 observation
- 복수 intent hypothesis와 confidence
- affect hypothesis와 confidence
- conversational need와 temporal scope
- response contract
- memory, open-loop, project, improvement proposal
- uncertainty

Observation과 각 hypothesis/proposal의 evidence는 현재 원문 또는 저장된 context의 정확한
연속 부분문자열이어야 한다. confidence는 `0.0~1.0`, temporal scope와 memory kind는
허용 목록만 쓸 수 있다. Qwen이 JSON 하나를 정확히 `json` 코드펜스로 감싸는 경우만
제한적으로 허용하며, 코드펜스 앞뒤 설명문은 거부한다.

## 사용법

일반 `./winter chat`, phone web, Voice 대화는 이벤트를 `pending`으로만 추가한다. 실제
응답 지연을 만들지 않기 위해 같은 턴에서 분석하지 않는다.

```bash
# 아직 처리하지 않은 이벤트를 Orin local LLM으로 최대 10개 분석
./winter chat --analyze-pending-turns --analysis-limit 10

# raw event, 상태와 분석 JSON 또는 실패 원인 조회
./winter chat --list-turn-understanding
```

오프라인 orchestration 검증에서만 fake를 명시한다. fake는 의도나 기억 후보를 추론하지
않고 deterministic한 빈 분석을 반환한다.

```bash
docker compose run --rm dev python -m companion.cli \
  --backend fake \
  --turn-understanding-db /workspace/data/turn_understanding.sqlite \
  --analyze-pending-turns
```

분석 명령은 실패 수가 하나라도 있으면 exit code 1을 반환하고 `failed` 원인을 DB에 남긴다.
운영 중 실제 모델 실패를 fake 결과로 바꾸지 않는다.

## 2026-08-15 실제 검증

Orin의 local llama-server에 격리된 임시 이벤트를 보냈다. 최초 결과는 유효한 JSON을
Markdown 코드펜스로 감싸 strict JSON 단계에서 실패했고, 이벤트는 예상대로 `failed`로
보존됐다. 정확히 하나의 `json` 코드펜스만 벗기도록 보완한 뒤 같은 유형의 새 발화는
`completed=1, failed=0`으로 schema와 exact-evidence 검증까지 통과했다.

그 뒤 10개 synthetic held-out scene으로 전체 품질을 측정했다. llama.cpp JSON Schema와
분석 전용 deterministic sampling을 적용한 최종 결과는 **5/10 scene, 37/45 check**다.
순간 감정·회상 불확실성·capability gap·intent correction·제3자 분리는 통과했지만,
직접 선호와 프로젝트 결정의 temporal/memory proposal 및 모호한 반응 evidence가 activation
기준에 미달했다. 상세 결과와 재현 명령은
[Held-out Evaluation](turn-understanding-evaluation.md)에 기록한다.

## 현재 한계와 다음 gate

- 분석은 명시적 CLI 명령으로 실행하며 background scheduler는 아직 없다.
- 실패 이벤트를 자동 재시도하거나 수정하지 않는다. 원본 audit event는 그대로 보존한다.
- 첫 held-out baseline은 activation gate를 통과하지 못했다. 더 넓은 실제 대화와 천우의
  다음 반응으로 precision, false-memory proposal, temporal-scope 오류를 계속 측정해야 한다.
- 분석 결과는 아직 겨울이의 답변과 Memory에 아무 영향도 주지 않는다.
- 다음 구현은 천우의 correction, acceptance와 후속 행동을 이전 가설에 연결하는
  `OutcomeEvaluator`다. 이 검증 전에는 추론 memory를 자동 활성화하지 않는다.
