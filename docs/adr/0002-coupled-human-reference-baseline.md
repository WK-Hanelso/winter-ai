# ADR-0002: Coupled Human Reference Baseline

- Status: Accepted
- Date: 2026-08-07
- Issue: [#67](https://github.com/WK-Hanelso/winter-ai/issues/67)
- Epic: [#66](https://github.com/WK-Hanelso/winter-ai/issues/66)

## Context

현재 `CompanionCore`는 CLI와 Voice에 같은 `CompanionResponse`를 전달하지만, verbal
style과 prosody는 소수의 `dialogue_act`에서 각각 선택된다. 이 구조는 interface 공유는
보장하지만 실제 사람의 말투, 목소리와 상황별 행동이 어떻게 결합되는지는 설명하지 못한다.

특정 사람의 script는 그 사람의 음역, 호흡, 속도, 휴지와 사회적으로 학습한 전달 방식에
맞춰진 결과다. script만 다른 목소리로 읽으면 짧은 답이 무성의하게 들리거나, 의도된
휴지가 단순한 느린 발화로 들리는 등 Reference 자체를 왜곡할 수 있다.

동시에 데이터가 없는 상태에서 Qwen fine-tuning, 별도 world/appraisal model 또는 TTS
adaptation을 먼저 선택하면 어떤 구성요소가 실제 병목인지 검증할 수 없다.

## Decision

1. Human Reference는 `context + relationship + atmosphere + script + voice + delivery`가
   정렬된 하나의 관찰 단위로 취급한다.
2. Voice Identity, Prosody, Verbal Style은 교체 가능한 계약으로 분리하되, 하나의 Joint
   Utterance Planner가 함께 결정한다.
3. CLI와 Voice는 동일한 lexical response와 Companion 상태를 공유한다. Voice는 prosody와
   비언어 delivery를 추가하며 문장을 다시 생성하지 않는다.
4. 사용자가 명시적으로 선택한 실존 인물의 음성과 대화는 개인용·비공개 Reference
   기준선에서 함께 사용할 수 있다. 원본과 파생 데이터는 외장 저장소에만 둔다.
5. 먼저 prompt/few-shot baseline을 held-out scene에서 평가한다. 관찰된 실패에 따라
   Qwen LoRA/SFT, appraisal model, TTS adaptation을 각각 결정한다.
6. Human Reference는 최종 겨울이가 아니다. Reference 검증 뒤 별도 Milestone에서 text,
   voice, relationship behavior를 함께 변경한 Winter Delta를 정의한다.

## CLI and Voice parity

같은 lexical input과 같은 state에서는 두 interface가 같은 lexical response를 사용한다.
Voice input이 user prosody 같은 추가 신호를 제공하면 appraisal 결과가 달라질 수 있지만,
이는 별도 Companion이 아니라 입력 정보 차이다. 웃음, 한숨과 pause 같은 비언어 event는
response metadata로 유지하며 CLI 표시 정책은 별도 renderer 결정으로 남긴다.

## Consequences

- 같은 원본 corpus에서 text, delivery, voice와 joint evaluation view를 만들 수 있다.
- 말투 오류, 상황 판단 오류와 합성 음성 오류를 분리해서 측정할 수 있다.
- 데이터 정렬과 annotation 비용이 증가한다.
- 영상 편집, 대화 상대, 시기와 녹음 품질을 명시하지 않으면 잘못된 인과를 학습할 수 있다.
- 현재 `VerbalStylePlanner`와 `ProsodyPlanner` 통합은 후속 구현 이슈이며 이 ADR에서는
  코드를 변경하지 않는다.

## Rejected alternatives

### 사용자 선호만으로 겨울이를 직접 정의

검증 가능한 실제 인간 기준선이 없어 사용자의 현재 판단을 인간다움의 정답으로 과적합할
수 있다.

### Reference script만 수집하고 다른 TTS로 재생

문장과 원래 delivery의 결합을 잃어 Reference의 대화 행동을 왜곡한다.

### 먼저 Qwen 또는 TTS를 fine-tune

held-out baseline이 없으므로 학습 효과, 암기와 구성요소별 실패를 구분할 수 없다.
