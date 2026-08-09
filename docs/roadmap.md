# Roadmap

이 문서는 현재 작업 순서와 decision gate를 기록한다. 각 단계는 하나의 GitHub Issue로
진행하며, 검증과 보고가 끝난 뒤 다음 단계로 넘어간다.

## Current state

- Milestone 0: local LLM, STT, TTS 독립 probe 완료
- Milestone 1: shared Core와 CLI 경로 완료, 실제 Push-to-talk PR은 열려 있음
- Milestone 2: Identity와 explicit Memory lifecycle 기반 완료
- Milestone 3: Voice Identity 0.1 기반과 MeloTTS 평가 완료, CosyVoice 비교는 보류
- Milestone 4: Human Reference 선정 기준·외장 storage·multimodal schema 완료,
  소규모 실제 수집 probe는 사용자 입력 gate 대기

## Milestone 4 — Human Reference Baseline

Epic: [#66](https://github.com/WK-Hanelso/winter-ai/issues/66)

| Order | Work item | Output | Gate |
| ---: | --- | --- | --- |
| 1 | [설계 결정과 문서 기준선](https://github.com/WK-Hanelso/winter-ai/issues/67) | ADR, architecture, roadmap | 완료 |
| 2 | [Reference 선정 기준](https://github.com/WK-Hanelso/winter-ai/issues/69) | hard gate, rubric, evidence template | 완료 |
| 3 | [외장 저장소와 manifest](https://github.com/WK-Hanelso/winter-ai/issues/71) | 재현 가능한 storage contract | 완료 |
| 4 | [Multimodal schema](https://github.com/WK-Hanelso/winter-ai/issues/73) | scene alignment와 annotation schema | 완료 |
| 5 | [소규모 수집·정렬 probe](https://github.com/WK-Hanelso/winter-ai/issues/75) | 3시간 이하 raw source의 usable subset | 사용자 입력 후 사용 가능 비율과 수동 비용 기록 |
| 5a | [source metadata preflight](https://github.com/WK-Hanelso/winter-ai/issues/77) | 다운로드 없는 접근성·duration·format 확인 ([문서](reference-source-probe.md)) | cap 초과 또는 접근 실패 시 실제 수집 진행 거부 |
| 5b | [자막 품질 측정](https://github.com/WK-Hanelso/winter-ai/issues/82) | 자막의 `raw_transcript` 사용 가능성 판정 ([문서](reference-subtitle-quality.md)) | 편집 강도 측정 후 전사 방식 결정 |\n| 5c | [오디오 파일럿 수집](https://github.com/WK-Hanelso/winter-ai/issues/84) | source-003 오디오 확보 ([문서](reference-audio-ingest.md)) | 파일럿 성공 후 나머지 source 수집 여부 결정 |
| 5d | [STT 파일럿](https://github.com/WK-Hanelso/winter-ai/issues/86) | 구간 전사 품질 측정 ([문서](reference-transcription-probe.md)) | 전사 방식 확정 전 diarization 설계 필요 |
| 5e | [Voice Set 추가](https://github.com/WK-Hanelso/winter-ai/issues/88) | 단독 화자 source 확보와 STT 재측정 ([ADR](adr/0003-reference-transcription-strategy.md)) | 화자 분리 설계 후 전체 전사 진행 |
| 6 | 행동·분위기 annotation | held-out 가능한 behavior labels | inter-annotation/수동 검토 기록 |
| 7 | Reference Voice baseline | 원본 대비 동일 script 합성 | voice reproduction gap 분리 |
| 8 | CLI behavior baseline | prompt/few-shot Reference 응답 | held-out behavior score 기록 |
| 9 | Voice joint baseline | shared text + Reference delivery | text/voice/joint gap 분리 |
| 10 | 학습 방식 결정 | ADR과 benchmark | 필요한 component만 승인 |
| 11 | 수직 단면 검증 | 재현 명령·결과·한계 | Milestone 완료 보고 |

각 작은 Issue가 끝날 때 무엇 때문에 무엇을 했고, 그 결과 이제 무엇이 가능한지 사용자에게
보고한다. 데이터가 부족하거나 결과가 나쁘다는 이유만으로 다음 학습 단계를 자동 승인하지
않는다.

Order 5의 실제 source 접근 전에는 사용자가 Reference Human, source 목록과 전용 외장
storage 절대경로를 승인해야 한다. 실제 identity와 URL은 외장 private manifest에만 두며,
승인이나 mount 확인이 없으면 다운로드·storage write·synthetic fallback을 수행하지 않는다.

## Milestone 5 — Winter Character Foundation

검증된 Reference Human과의 `Winter Delta`를 정의한다. 말투, 음성, 관계 행동을 함께
변형하고 CLI와 Voice에서 독립적인 겨울이 OC로 인식되는지 평가한다.

## Milestone 6 — Continual Preference Update

겨울이의 Core Persona와 천우에게 적응하는 preference를 분리한다. 반복 관찰, 명시적
선호와 변경 이력을 통해 안정적으로 업데이트한다.

## Milestone 7 — Worker Integration

겨울이의 Identity를 외부 Worker에 넘기지 않고 전문 작업 결과만 위임한다. 최종 응답은
겨울이가 shared character policy로 재구성한다.
