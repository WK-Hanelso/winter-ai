# Phone Voice Real-time Readiness — 2026-08-17

## 결론

현재 폰 경로는 **실제 사용 가능한 push-to-talk 수직 단면**이지만, 자연스러운 실시간
대화 상태는 아니다. 기존 Whisper 경로의 실제 폰 3턴은 첫 음성까지 중앙값 9.68초였다.
SenseVoice 전환으로 STT는 synthetic 전체 Web 경로에서 0.35초까지 줄었다. 이어 기존
blind에서 현재 Seed-VC보다 높았던 Chatterbox S3Gen Reference를 직접 출력해, 분리된
Core probe의 첫 audio를 turn 시작 2.4초에 만들었다. 새 경로의 실제 폰 4턴은 first-play
p50 4.41초, p95 5.03초였다. 목표 표본 20턴 전이며 p50 3초 gate도 아직 넘으므로
자연스러운 실시간 대화로 확정하지 않는다.

## 현재 실제 흐름

```text
버튼을 누른 동안 브라우저가 녹음
  → 버튼을 떼면 WebM 전체 업로드
  → ffmpeg 변환 + SenseVoice 전체 파일 STT
  → local LLM이 첫 문장을 완성
  → Chatterbox가 S3Gen Reference 조건으로 첫 문장 직접 합성
  → SSE로 폰에 전송하고 이어폰 재생
```

출력은 첫 문장부터 시작한다. 이전 Seed-VC 직렬 단계는 제한 운영 경로에서 빠졌고,
컨테이너는 rollback용으로 보존한 채 중지했다. 입력은 streaming이 아니므로 말하는
동안 STT를 시작하지 못한다.

## 현재 장비 실측

로컬 서비스가 모두 실행 중인 상태에서 개인정보가 없는 같은 짧은 문장
`오늘 운동 다녀오느라 수고했어.`로 측정했다. 합성 결과 길이는 약 2.4~2.7초다.

| 구간 | 첫 probe | warm 3회 중앙값 | 비고 |
| --- | ---: | ---: | --- |
| Whisper STT | 3.62초 | 3.62초 | 교체 전 synthetic 기준 |
| SenseVoice model | 0.12초 | 0.12초 | 같은 2.36초 음성, Q8 CPU |
| SenseVoice Web 전체 | 0.35초 | 단일 측정 | ffmpeg·HTTP·model 포함 |
| local LLM 전체 짧은 답 | 0.55초 | 단일 측정 | 실제 Core 문맥에서는 기존 약 1.46초 기록 |
| Chatterbox 첫 문장 | 3.55초 | 2.20초 | 2.36초 WAV 생성 |
| Seed-VC 변환 | 4.72초 | 2.99초 | 2.7초 WAV 생성 |
| 이전 두 단계 고정 문장 | 4.82초 | 단일 측정 | Chatterbox 2.16 + Seed-VC 2.66 |
| 직접 S3Gen 고정 문장 | 2.95초 | 2.24초 | warm 2.21, 2.27초 |
| 직접 S3Gen Core probe | 2.40초 | 단일 측정 | 첫 문장 0.95초 포함, 실제 DB 분리 |
| 현재 직접 경로 실제 폰 | 5.03초 p95 | 4.41초 p50 | 4/20 turn, gate 판단 전 |
| 기존 버튼 해제→첫 음성 | 10.41초 p95 | 9.68초 p50 | 실제 폰 Whisper 3턴 |

기존 폰 3턴의 release-to-STT p50은 4.12초, 서버 STT p50은 3.79초였다. SenseVoice는
10.45초 Zeroth-Korean 공개 fixture를 0.49초에 정답과 정확히 일치시켰다. 이는 운영 후보
근거이지 일반 정확도 증명이 아니다. 실제 폰, 고유명사, 기술어, 소음과 긴 발화는 새
20-turn 기준선과 held-out suite에서 계속 확인한다.

새 실제 폰 4턴의 release-to-STT p50은 0.90초였다. 다만 수동 audit에서 4개 transcript
중 2개에 의미를 훼손하는 치환이 보였다. 속도 gate와 별도로 spontaneous Korean content
fidelity gate가 필요하며, 이 표본만으로 정확도를 승인하지 않는다.

## 사용성 판정

| 경험 | 현재 판정 |
| --- | --- |
| 폰에서 말하고 이어폰으로 답 듣기 | 가능 |
| CLI와 같은 정체성·대화·Memory 사용 | 가능 |
| 첫 문장부터 순차 재생 | 가능 |
| 말이 끝난 뒤 1~2초 안에 반응 | 불가능 |
| 말하는 동안 실시간 STT | 없음 |
| 자동 turn 종료 | 없음; push-to-talk |
| 겨울이 말 도중 끼어들기 | 없음 |
| 자연스러운 사람 수준 음질·질문 억양 | 미달 |
| 이동 중 네트워크 단절 복구 | 미검증 |

따라서 집이나 테스트 환경에서 천천히 주고받는 사용은 가능하다. 매일 이어폰을 낀 채
사람처럼 즉각 대화하는 제품 기준에는 아직 도달하지 않았다.

## 실제 폰 20-turn 계측

2026-08-17부터 폰 브라우저는 별도 버튼이나 명령 없이 성공한 Voice turn의 시간을
기록한다. 기존 Whisper 3턴은 `data/voice_latency.sqlite`에 보존하고, 중간 SenseVoice
2단계 경로는 `data/voice_latency-sensevoice.sqlite`, 현재 직접 voice 표본은
`data/voice_latency-sensevoice-direct.sqlite`에 쌓는다. 성능 DB에는 대화 text,
전사문, 녹음과 합성 audio를 넣지 않는다. 임의 trace ID와 다음 숫자만 보존한다.

- 버튼 해제→STT 응답
- 버튼 해제→첫 문장 수신
- 버튼 해제→첫 audio 수신
- 버튼 해제→브라우저의 실제 `playing` event
- 서버 내부 STT, 첫 문장, 첫 audio 시간

같은 trace의 브라우저 재전송은 한 번만 저장한다. 최근 성공 20 turn의 p50/p95를
계산하며 20개 전에는 `gate_ready=false`다. 개발자는 local/Tailscale Web의
`GET /voice-metrics`에서 text 없는 요약만 확인할 수 있다. Web 서버 log에도 새 sample이
들어올 때마다 현재 표본 수와 first-play p50/p95가 찍힌다.

현재 한계는 **첫 재생까지 성공한 turn만 latency 표본이 된다**는 점이다. STT 실패,
network 단절, browser autoplay 실패까지 포함한 success rate와 failure-stage 집계는 다음
계측 확장에 남긴다. 따라서 이번 p95는 성공 turn의 속도 근거이며 전체 신뢰성 지표로
과장하지 않는다.

Web은 `deploy/systemd/winter-web.service`의 user service로 실행한다. 로그인한 user
manager 안에서 장애 시 2초 뒤 재시작한다. 현재 host의 linger는 꺼져 있으므로 로그인
전부터 서비스를 올리는 배포는 아직 아니다. 폰이 이전 JavaScript를 재사용해 계측을
누락하지 않도록 HTML과 JSON 응답은 `Cache-Control: no-store`로 제공한다.

Issue #114의 repository unit과 Web 기본값은 `127.0.0.1:8000` loopback, POST body 16 MiB
상한으로 변경했다. Tailscale Serve가 유일한 원격 ingress가 되는 구조다. 실행 중 service는
unit 설치·재시작과 실제 socket 확인 전까지 이전 상태일 수 있으므로, 이 변경은 운영 확인
전에는 Shadow로 판정한다. 세부 절차는 [Web ingress safety](web-ingress-safety.md)를 따른다.

## 다음 고도화 순서

속도 문제를 prompt나 대화 코드 수정으로 해결하지 않는다. LLM은 현재 경로에서 가장
빠른 구간이고, 병목은 음성 입출력이다.

1. **STT·직접 voice 경로 교체 완료:** 실제 폰 성공 turn 20개로 새 기준선을 확정한다.
2. 일상 대화에서 구절 반복, 발음과 질문 억양을 기록해 제한 운영 유지 여부를 판정한다.
3. 말하는 동안의 chunk 전송은 release 이후 STT가 0.5초 미만이므로 다시 효과를 계산한다.
4. first-audio p50이 충분히 내려간 뒤 VAD/end-of-turn을 추가한다.
5. 마지막으로 barge-in, 취소, 네트워크 재연결을 검증한다.

목표 gate는 우선 `버튼 해제→첫 음성 p50 3초 이하, p95 5초 이하`로 둔다. 이 gate를
통과하기 전에는 “실시간 Voice”가 아니라 “push-to-talk Voice”로 표기한다.
