# Voice Identity 0.1 and Human Reference boundary

기존 Voice Identity 0.1은 runtime 독립적인 prosody 전달 경로를 확인하기 위한 초안이다.
Milestone 4에서는 사용자가 명시적으로 선택한 실존 인물의 대화와 음성을 개인용·비공개
Human Reference로 함께 분석한다. 이는 script와 실제 delivery를 분리해 Reference를
왜곡하지 않기 위한 결정이며, 최종 겨울이 Voice를 바로 확정한다는 뜻은 아니다.

음색을 만드는 TTS runtime, Prosody, Verbal Style은 교체 가능한 계약으로 분리한다. 그러나
응답을 만들 때는 하나의 Joint Utterance Plan에서 함께 선택해야 한다. CLI는 plan의 동일한
lexical response를 표시하고, Voice는 같은 text와 delivery metadata를 TTS에 전달한다.

| Profile | 사용 초안 | pace | energy | pitch |
| --- | --- | ---: | ---: | ---: |
| neutral | 일반 답변 | 1.00 | 1.00 | 0.0 |
| calm | 안정·지원 | 0.90 | 0.80 | -0.5 |
| warm | 기억 후보 안내 | 0.96 | 0.90 | +0.3 |
| serious | 경고 | 0.92 | 0.85 | -0.2 |

설정은 `configs/voice/base.py`, 선택 정책은 `companion.voice_profile.ProsodyPlanner`에
있다. `SpeechRequest`는 모든 값을 TTS adapter에 전달하지만, 현 MeloTTS adapter가 실제로
지원하는 제어 범위는 다음 평가 작업에서 측정한다.

이 값들은 최종 음성 품질이나 감정 상태의 주장이 아닌 초기 전달 경로다. Human Reference
baseline이 준비되면 실제 scene의 pace, pause, pitch, energy와 비언어 event를 바탕으로
교체하거나 확장한다. 상세 설계는 [Human Reference 문서](human-reference-design.md)와
[ADR-0002](adr/0002-coupled-human-reference-baseline.md)를 따른다.
