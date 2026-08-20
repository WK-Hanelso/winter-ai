# ADR-0030: 겨울이의 대화 시간과 전문 작업 완료 시간을 분리한다

- 상태: Accepted, Shadow only
- 날짜: 2026-08-20
- Issue: #126

## 문제

첫 실제 repository canary는 98.77초에 끝났다. source 불변과 typed evidence는 유지했지만
천우는 그동안 겨울이와 대화할 수 없었다. Worker가 아무리 정확해도 매 전문 작업이 현재
Voice turn을 1분 이상 붙잡는다면 Personal Companion으로 사용할 수 없다.

전문 코드 진단을 3초 안에 끝내는 것은 현재 Local Core와 외부 Worker로 현실적이지 않다.
대신 다음 두 시간을 제품 계약에서 분리한다.

- `conversation response`: 겨울이가 접수하고 대화를 계속할 수 있기까지의 시간
- `work completion`: 전문 결과가 검증되어 돌아오기까지의 시간

## 결정

개발용 `--winter-work-canary`에서 한 번에 하나의 read-only 작업만 background coordinator에
맡긴다.

```text
천우의 진단 요청
→ deterministic route
→ 즉시 “응, 내가 확인해볼게. 끝나면 내가 다시 말해줄게.”
→ 현재 turn 종료, 녹음 버튼 복구
→ background Local brief → typed Worker → Local review
→ Web `/work-updates` polling
→ 겨울이 text + Winter voice 재생
```

- 두 번째 전문 작업은 첫 작업이 끝나기 전 실행하지 않는다.
- 일반 대화는 background Worker 완료를 기다리지 않는다.
- 완료 결과는 Conversation DB에 assistant message로 추가되고 Web inbox에서 한 번 전달된다.
- 실패도 inbox에 남기며 Local 답변으로 위장하지 않는다.
- Web은 현재 turn 또는 음성 재생 중에는 결과 polling을 미뤄 겨울이가 자기 말을 끊지 않게
  한다.
- 기본 CLI/Web, systemd와 phone 운영 경로에는 flag를 켜지 않는다.

첫 canary에서 드러난 최종 판단 과장도 같은 범위에서 줄였다. Local Core는 Worker report의
evidence-backed claim 중 `핵심 판단 하나`와 `추천 하나`를 원문 그대로 선택한다. 정책 코드는
선택된 claim의 evidence ID를 직접 계산하고 그 밖의 새 원인·수치·구현 방법을 final response에
추가하지 않는다. 이것은 Worker 보고서 전체를 중계하는 것이 아니라 겨울이가 무엇을 핵심과
다음 행동으로 채택할지 선택하되, 선택되지 않은 사실을 지어내지 못하게 하는 계약이다.

## 실제 측정

동일한 repository 지연 질문을 Local Core `:18080`과 실제 typed Codex Worker로 다시 실행했다.
Conversation DB는 synthetic in-memory repository를 사용했고 source는 실행 전후 불변이었다.

| 구간 | 결과 |
| --- | ---: |
| background 접수 text | 0.000초 |
| 접수 직후 일반 Local 대화 | 2.138초 |
| 접수 첫 문장 실제 Chatterbox TTS | 2.450초 |
| 전문 작업 전체 | 82.830초 |

따라서 전문 작업 전체는 여전히 느리지만, 그 작업이 진행되는 동안 겨울이의 다음 대화는
막히지 않았다. 접수 첫 음성은 서버 구성요소 측정상 3초 목표 안이지만 실제 phone의 network,
SSE 수신과 재생 시작을 포함한 end-to-end 측정은 아니다.

새 claim 선택 gate는 지난 실행의 근거 없는 TTS warm pool·streaming·browser buffering
자유 작문을 차단했다. 이번 결과는 현재 기록이 성공 turn만 포함하고 Core/TTS/browser 구간을
분리하지 않아 가장 큰 세부 병목을 확정할 수 없다고 판단했다. 안전하게 멈춘 것은 맞지만
천우가 요청한 다음 한 가지 개선을 확정하지 못했으므로 `completed`가 아니라 `partial`이다.
음성 답변은 첫 번째 핵심 한계만 말하고 전체 limitations/evidence는 delivery payload에 함께
둔다. 아직 persistent audit은 아니다.

offline 회귀 810개, Ruff 전체와 `mypy src experiments` 172개 source file이 통과했다.

## 의도적으로 남긴 한계

- coordinator와 inbox는 process memory에만 있어 Web/CLI 재시작 시 진행 작업과 미전달 결과가
  사라진다.
- background brief/review와 일반 대화가 같은 Local LLM server를 사용한다. 한 실제 follow-up은
  2.138초였지만 generation 충돌 0건을 보장하는 priority scheduler는 없다.
- Web polling과 completion voice는 offline HTTP 회귀만 통과했고 실제 phone에는 배포하지 않았다.
- Worker report claim과 source text의 의미 일치는 provider와 사람 평가가 필요하다. stable
  evidence ID만으로 의미적 참을 완전히 증명하지 못한다.
- 취소, 진행률, timeout 뒤 재개와 persistent retry가 없다.
- 실제 task 품질은 partial이며 20-task activation gate로 갈 수 없다.

## 다음 gate

이 경로를 운영에 켜기 전에 다음을 순서대로 확인한다.

1. SQLite job/inbox로 process restart 뒤 pending·completed 결과를 복구한다.
2. foreground Local 대화를 우선하는 model scheduling 또는 별도 runtime 경계를 검증한다.
3. 실제 phone에서 접수 first-play p50 3초 이하, 작업 중 일반 대화 차단 0을 측정한다.
4. 동일 질문에서 근거 있는 다음 행동 하나를 선택하거나, 근거 부족을 천우가 이해할 수 있는
   짧은 partial 결과로 전달한다.
5. 그 뒤에만 서로 다른 read-only 실제 작업 20개를 사람 평가한다.
