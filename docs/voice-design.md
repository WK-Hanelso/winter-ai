# Voice Identity 0.1

겨울이의 Voice Identity는 특정 실존 인물의 목소리를 복제하지 않는다. 음색을 만드는 TTS
runtime과, 어떤 상황에 어떻게 말할지를 정하는 Prosody profile을 분리한다.

| Profile | 사용 초안 | pace | energy | pitch |
| --- | --- | ---: | ---: | ---: |
| neutral | 일반 답변 | 1.00 | 1.00 | 0.0 |
| calm | 안정·지원 | 0.90 | 0.80 | -0.5 |
| warm | 기억 후보 안내 | 0.96 | 0.90 | +0.3 |
| serious | 경고 | 0.92 | 0.85 | -0.2 |

설정은 `configs/voice/base.py`, 선택 정책은 `companion.voice_profile.ProsodyPlanner`에
있다. `SpeechRequest`는 모든 값을 TTS adapter에 전달하지만, 현 MeloTTS adapter가 실제로
지원하는 제어 범위는 다음 평가 작업에서 측정한다.

이 값들은 최종 음성 품질이나 감정 상태의 주장 아닌, 사용자 피드백으로 조정할 초기
말하기 규칙이다.
