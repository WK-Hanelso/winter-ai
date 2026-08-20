# ADR-0029: 기반 확장보다 실제 작업 한 건으로 가치를 판정한다

- 상태: Accepted, Shadow only, quality gate failed
- 날짜: 2026-08-20
- Issue: #126

## 문제

기존 경로는 typed read-only gateway, 격리, lifecycle과 rollback 같은 안전 경계를 많이
증명했지만 천우가 평범하게 한 작업 요청을 겨울이가 이해하고, Worker를 지휘한 뒤, 자기
판단으로 유용하게 답하는 제품 경험은 증명하지 못했다. 기반을 더 추가하면 실제 가치가 없는
구조만 커질 위험이 있었다.

이번 canary의 질문은 하나였다.

> 겨울이의 실제 대답 지연에서 어느 구간이 가장 큰지 현재 코드와 저장된 latency 근거로
> 진단하고, 다음 한 가지 개선을 추천해줘.

## 결정

새 framework나 state machine을 만들지 않고 기존 구성요소를 한 번만 연결한다.

```text
평범한 project 진단 요청
→ deterministic read-only 후보 gate
→ 같은 Local Winter Core가 목적·결과·완료 조건만 정리
→ typed Codex Worker가 목록·검색·텍스트 읽기로 근거 보고
→ 같은 Local Winter Core가 근거와 한계를 검토
→ 겨울이의 문장으로 답변
```

- `--winter-work-canary` 또는 `WINTER_WORK_CANARY=1`에서만 켜진다.
- 운영 systemd와 기본 CLI/Web에는 켜지 않는다.
- 명시적 기존 Codex 호출이 우선하며, 일반 대화·write 요청·대화 원문·Memory·Identity·계정
  정보가 포함된 요청은 이 경로로 보내지 않는다.
- Worker에는 사용자 원문이나 대화 context가 아니라 Local Core가 만든 제한된
  `WorkerTaskPacket`만 전달한다.
- Worker는 전체 host repository를 직접 보지 않고 privacy-safe copy를 typed gateway로
  읽는다. 선택해 읽은 project text와 packet은 외부 Codex 처리 경로로 전달될 수 있다.
- Worker 전후 privacy-safe source baseline이 다르면 실패한다.
- 준비 중 문구는 화면 status가 아니라 겨울이가 실제 문장으로 말한다.
- 실패하면 일반 Local 답변으로 위장하지 않고 확인하지 못했다고 말한다.
- 자동 retry는 하지 않는다.

개발자가 실패를 재현할 때만 다음처럼 명시적으로 실행한다. 정상 사용 명령이 아니다.

```bash
./winter chat \
  --winter-work-canary \
  --codex-executable /home/hanelso/.nvm/versions/node/v20.20.2/bin/codex
```

기존 V2.1 전체 Intent 계약은 이 한정된 canary에서 사용하지 않는다. 권한과 resource는
deterministic gate가 이미 `project.inspect`와 `project`로 고정하고, Local Core는 천우의
목적·원하는 결과·완료 조건만 정리한다. 이것은 모델에게 권한 판단을 맡기는 예외가 아니라
의미 판단과 권한 강제를 분리한 것이다.

## 실제 검증

동일 질문으로 세 번 실행했다.

1. 첫 실행은 약 117초 뒤 Worker의 check가 사용한 evidence ID가 보고서 evidence 목록에
   없어서 검증에서 차단됐다. source는 변하지 않았고 답변을 만든 척하지 않았다.
2. 두 번째 실행은 약 109초 뒤 typed Worker 보고까지 통과했지만 기존 전체 review JSON이
   token 상한에서 잘려 차단됐다. source는 변하지 않았다.
3. 제한된 review 계약으로 바꾼 세 번째 실행은 98.77초에 끝까지 완주했다. 문서 근거 세 개와
   실제 한계를 반환했고 source는 변하지 않았다.

세 번째 실행도 **제품 품질은 실패**다. 최종 답변은 한 가지 추천을 요구한 질문에 TTS warm
pool, streaming 시작 시점, browser buffering을 함께 제시했다. 인용된 기록은 음성 입출력이
병목이고 다음 단계가 실제 폰 성공 turn 20개 수집이라는 점까지만 뒷받침한다. 답변에 함께
붙은 한계도 Core/TTS/browser 구간을 아직 분리할 수 없다고 인정한다. 따라서 이 답변은
근거가 허용하는 범위를 넘어갔고, 천우에게 제공할 Winter 판단으로 채택할 수 없다.

기본 offline 회귀 804개, Ruff 전체와 `mypy src experiments` 172개 source file을 통과했다.

## 결과

다음은 증명했다.

- 천우가 Worker 이름을 말하지 않아도 한정된 read-only 요청을 후보로 찾을 수 있다.
- 겨울이가 먼저 확인한다고 말하고, Worker에는 제한된 목적만 보내며, 결과를 같은 Core로
  다시 검토하는 경로가 기술적으로 끝까지 돈다.
- source 불변, typed evidence와 fail-closed 경계가 실제 외부 Worker 실행에서도 유지된다.

다음은 증명하지 못했다.

- Local Core가 Worker 근거에서 가장 중요한 한 가지를 골라 근거 범위 안에서 설명하는 능력
- 사용자가 기다릴 수 있는 latency
- 일반 project 요청으로의 확장 가능성
- 운영 Web/Voice에서의 실제 사용성

따라서 #126 구현은 Shadow evidence로 보존하되 운영 activation, service restart와 20-task
확장을 하지 않는다. 다음 작업은 prompt 문구를 더 누적하는 일이 아니다. 후속
[ADR-0030](0030-nonblocking-winter-owned-work.md)은 판단 단위를 evidence-backed claim
선택으로 제한하고 전문 작업을 background로 분리했다. 새 사실 생성은 막았지만 결과는
partial이므로, 어떤 경우에도 Worker 보고서를 그대로 읽는 것을 Winter의 완성된 판단으로
부르지 않는다.
