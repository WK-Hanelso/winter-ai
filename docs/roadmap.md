# Roadmap

이 문서는 현재 작업 순서와 decision gate를 기록한다. 각 단계는 하나의 GitHub Issue로
진행하며, 검증과 보고가 끝난 뒤 다음 단계로 넘어간다.

## Current state

- Milestone 0: local LLM, STT, TTS 독립 probe 완료
- Milestone 1: shared Core와 CLI 경로 완료, 실제 Push-to-talk PR은 열려 있음
- Milestone 2: Identity와 explicit Memory lifecycle 기반 완료
- Milestone 3: Voice Identity 0.1 기반과 MeloTTS 평가 완료, CosyVoice 비교는 보류
- Milestone 4: Human Reference 선정 기준·외장 storage 계약 완료, multimodal schema 검증 중

## Milestone 4 — Human Reference Baseline

Epic: [#66](https://github.com/WK-Hanelso/winter-ai/issues/66)

| Order | Work item | Output | Gate |
| ---: | --- | --- | --- |
| 1 | [설계 결정과 문서 기준선](https://github.com/WK-Hanelso/winter-ai/issues/67) | ADR, architecture, roadmap | 완료 |
| 2 | [Reference 선정 기준](https://github.com/WK-Hanelso/winter-ai/issues/69) | hard gate, rubric, evidence template | 완료 |
| 3 | [외장 저장소와 manifest](https://github.com/WK-Hanelso/winter-ai/issues/71) | 재현 가능한 storage contract | 완료 |
| 4 | [Multimodal schema](https://github.com/WK-Hanelso/winter-ai/issues/73) | scene alignment와 annotation schema | synthetic fixture validation |
| 5 | 소규모 수집·정렬 probe | 2~3시간 이하 raw source의 usable subset | 사용 가능 비율과 수동 비용 기록 |
| 6 | 행동·분위기 annotation | held-out 가능한 behavior labels | inter-annotation/수동 검토 기록 |
| 7 | Reference Voice baseline | 원본 대비 동일 script 합성 | voice reproduction gap 분리 |
| 8 | CLI behavior baseline | prompt/few-shot Reference 응답 | held-out behavior score 기록 |
| 9 | Voice joint baseline | shared text + Reference delivery | text/voice/joint gap 분리 |
| 10 | 학습 방식 결정 | ADR과 benchmark | 필요한 component만 승인 |
| 11 | 수직 단면 검증 | 재현 명령·결과·한계 | Milestone 완료 보고 |

각 작은 Issue가 끝날 때 무엇 때문에 무엇을 했고, 그 결과 이제 무엇이 가능한지 사용자에게
보고한다. 데이터가 부족하거나 결과가 나쁘다는 이유만으로 다음 학습 단계를 자동 승인하지
않는다.

## Milestone 5 — Winter Character Foundation

검증된 Reference Human과의 `Winter Delta`를 정의한다. 말투, 음성, 관계 행동을 함께
변형하고 CLI와 Voice에서 독립적인 겨울이 OC로 인식되는지 평가한다.

## Milestone 6 — Continual Preference Update

겨울이의 Core Persona와 천우에게 적응하는 preference를 분리한다. 반복 관찰, 명시적
선호와 변경 이력을 통해 안정적으로 업데이트한다.

## Milestone 7 — Worker Integration

겨울이의 Identity를 외부 Worker에 넘기지 않고 전문 작업 결과만 위임한다. 최종 응답은
겨울이가 shared character policy로 재구성한다.
