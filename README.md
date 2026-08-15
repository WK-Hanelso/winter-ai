# winter-ai

로컬 중심 Personal Companion을 만드는 프로젝트입니다. 같은 사용자의 대화,
기억, 선호와 관계 맥락을 장기간 유지하면서 CLI와 Voice에서 하나의 정체성으로
동작하는 것을 목표로 합니다.

Winter V1의 최상위 목표는 천우와의 대화에서 의도와 결과를 이해하고, 근거 있는 기억과
자기 상태를 유지하며, 자신의 실제 한계와 개선 방향을 천우와 토론한 뒤 승인된 변경만
격리된 Worker로 구현·검증·채택하는 **Level 3 공동 자기발전 Companion**입니다. 전체
제품 계약과 완료 조건은 [Winter V1 Product Goal](docs/winter-v1-goal.md)을 따릅니다.
각 기능의 현재 성숙도·검증 근거·남은 고도화 항목은
[V1 Maturity Ledger](docs/v1-maturity-ledger.md)에 지속적으로 기록합니다.

## 현재 상태

Milestone 0~2의 기반 구조, Identity와 명시적 Memory lifecycle에 더해 실제 local
voice 수직 단면이 동작합니다. CLI와 폰 브라우저는 같은 `CompanionCore`, Identity와
SQLite 대화를 사용합니다. 폰의 push-to-talk 녹음은 whisper.cpp STT를 거쳐 Qwen에
전달되고, 답은 Chatterbox와 Seed-VC가 문장 단위 WAV로 만들어 SSE로 돌려줍니다.
대화 모델은 Orin의 local llama-server를 SSH loopback tunnel로 사용하며 외부 상용
모델 API를 호출하지 않습니다.

`나는 … 좋아해`, `내 생일은 …이야`처럼 사용자가 직접 단정한 안정적인 진술은
`기억해`가 없어도 즉시 active Memory가 됩니다. 명시적 `기억해` 요청도 저장과 승인을
함께 한 것으로 처리합니다. 순간 상태·질문·추측·제3자 사실과 모델이 추론한 내용은
자동 활성화하지 않습니다. 수정 이력, 논리적 폐기, 명시적 물리 삭제를 지원합니다.
M2의 수직 단면 검증 결과와 알려진 한계는
[M2 검증 문서](docs/milestone-2-validation.md)에 기록합니다.

Voice Identity 0.1은 Python config의 `neutral`, `calm`, `warm`, `serious` Prosody
profile로 시작합니다. 이는 실제 음색 모델과 분리된 말하기 계획이며, 설계 초안은
[Voice 설계 문서](docs/voice-design.md)에 기록합니다.

V1의 `TurnUnderstanding Shadow Mode` 수직 단면이 구현되어, 응답과 Memory를 바꾸지
않고 의도·감정·시간 범위·기억 및 개선 신호를 별도 SQLite에 구조화해 기록하고 Orin
local LLM으로 사후 분석할 수 있습니다. 10개 held-out 장면의 첫 평가는 5/10으로
activation gate를 통과하지 못했으므로 결과는 여전히 답변·Memory에 주입하지 않습니다.
설계와 사용법은 [TurnUnderstanding 문서](docs/turn-understanding.md), 측정 결과는
[held-out evaluation](docs/turn-understanding-evaluation.md)을 따릅니다. Voice 연구
track의 현재 단계는 [Milestone 4 — Human Reference Baseline](https://github.com/WK-Hanelso/winter-ai/issues/66)입니다.
실제 한 사람의 대화 맥락, 말투, 분위기와 목소리를 같은 시간축에 정렬하고, CLI와 Voice가
공유할 수 있는 행동 기준선을 먼저 검증합니다. CLI는 shared lexical response를 표시하고,
Voice는 동일한 text와 delivery plan을 local TTS로 실현합니다. 결합 기준 설계 #67은
완료됐고 Reference 선정 기준 #69와
[외장 storage 계약 #71](https://github.com/WK-Hanelso/winter-ai/issues/71)도 확정했습니다.
멀티모달 장면 schema #73도 완료해 맥락·전후 분위기·원문/정규화 문장·음성 전달을
source-relative 시간축에 정렬하고 검증할 수 있습니다. Reference 원본과 파생 데이터는
승인된 외장 storage에만 있으며 Git에서 제외됩니다. 실제 corpus 채굴과 정제를 거쳐
89.4분의 Seed-VC 학습까지 완료했지만, F0 없는 변환이 억양을 누르는 것으로 측정되어
추가 학습은 중단했습니다. 현재 voice 경로의 비교와 다음 결정은
[Voice path decision gate](docs/voice-path-decision.md)에 기록합니다.
구조, 데이터 경계와 학습 decision gate는
[Human Reference 설계](docs/human-reference-design.md), [ADR-0002](docs/adr/0002-coupled-human-reference-baseline.md),
[선정 기준](docs/reference-human-selection.md), [외장 storage](docs/reference-data-storage.md),
[장면 schema](docs/reference-scene-schema.md), [roadmap](docs/roadmap.md)에 기록합니다.

## 겨울이 시작하기

Orin의 llama-server와 `scripts/orin-tunnel.sh`가 실행 중이면 아래 명령으로
시작합니다. 대화·기억·Identity는 모두 Host의 `data/`에 지속 저장됩니다. `start`는
기본 터널 주소 `http://127.0.0.1:18080`의 health 확인을 마친 뒤 겨울이 CLI를
엽니다.

```bash
./winter start
```

다른 local llama-server를 의도적으로 시험할 때만 `.env`의
`WINTER_LLM_URL`을 바꿉니다. Host에서 Python 3.11을 자동으로 찾지 못하면
`WINTER_PYTHON`에 실행 파일 경로를 지정합니다. Compose로 띄우는 구형 로컬
llama-server의 모델 경로 설정은 `.env.example`에 별도로 남겨 두었습니다.

개발 중 모델 없이 화면 흐름만 확인하려면 `--backend fake`를 명시합니다.

```bash
docker compose run --rm dev python -m companion.user_cli --backend fake
```

이미 실행 중인 겨울이에 다시 연결하려면 `./winter chat`, 상태 확인은
`./winter status`를 사용합니다. `./winter stop`은 비교용 legacy PC llama-server만
멈추며, 실제 Orin server와 SSH tunnel은 각 Host의 service로 관리합니다.

겨울이의 대답을 소리로 들으려면 `./winter voice`를 사용합니다. 타자로 묻고
음성으로 듣는 디버그 경로입니다. 마이크 입력은 폰 브라우저의 push-to-talk 웹
인터페이스에 구현되어 있으며 whisper.cpp 서버와 동일한 Core를 사용합니다.

```bash
./winter voice                      # 대화
./winter voice --say "안녕" --no-play  # 한 문장만, 재생 없이 wav로
```

`voice`만은 다른 명령과 달리 dev container가 아닌 **Host에서** 실행됩니다.
합성이 `docker run`을, 재생이 Host 사운드 장치를 필요로 하기 때문입니다. text CLI와
마찬가지로 기본 `WINTER_LLM_URL=http://127.0.0.1:18080`의 Orin SSH tunnel을 사용하고,
대화·기억·Open Loop·TurnUnderstanding도 같은 Host `data/`를 사용합니다.
자세한 구조와 시행착오는 [음성 출력 경로](docs/voice-path.md)에 있습니다.

## 과거 대화를 실제 기억으로 정리하기

대화 원문 저장과 장기 기억은 서로 다른 단계입니다. 모든 CLI·Web 대화 원문은
`conversations.sqlite`에 보존하지만, 겨울이가 다음 대화에 사용할 장기 기억은 별도의
검토를 통과해야 합니다.

```bash
./winter overview   # 원문, 분석 대기, 기억 후보와 활성 기억 수 확인
./winter memories   # 실제 원문 근거를 읽고 승인·수정·거절
```

과거 대화 정리는 최근 맥락과 현재 사용자 발화를 local LLM으로 읽고, 몇 주 뒤 다른
대화에서도 유효한 사실·선호·결정·프로젝트·지원 방식만 후보로 제안합니다. 후보 본문은
모델이 다시 써낸 요약이 아니라 사용자가 실제로 말한 원문입니다. 따라서 모델이 없는
사실을 기억 문장에 끼워 넣을 수 없고, 후보는 사용자가 승인하기 전까지 답변에
주입되지 않습니다.

승인된 정보 중 `current_state:*`는 안정적인 프로필과 분리됩니다. 예를 들어 현재의
멘탈, 건강, 업무 스트레스는 겨울이가 대화에서 참고하지만 천우의 영구 성격으로
취급하지 않습니다. 같은 topic의 새 상태를 확인하면 이전 값은 `deprecated`가 되고
최신 값 하나만 사용됩니다.

현재 상태와 이전 상태의 Timeline은 모델 없이 확인할 수 있습니다.

```bash
./winter state
```

평범한 대화에는 최신 상태만 전달합니다. “예전과 지금 내 마음 상태가 어떻게 달라?”처럼
상태 변화를 묻는 대화에만 과거 값을 함께 전달하며, 천우가 직접 말하지 않은 변화의
원인은 추측하지 않도록 제한합니다.

기억 정리 분석은 대화용 Orin을 자동으로 사용하지 않습니다. 실제 부하에서 Orin 보드가
재부팅된 기록이 있으므로, 안전한 별도 local llama-server를 준비한 경우에만 실행합니다.

```bash
WINTER_ANALYSIS_LLM_URL=http://127.0.0.1:18081 ./winter reflect
```

현재 구조, 검증 결과와 남은 한계는
[대화 기억 복구 설계](docs/reflection-memory.md)에 기록합니다.

초기 Local LLM은 Qwen3-4B-Instruct-2507 Q4_K_M으로 검증했습니다. 현재 실제 Orin
endpoint는 `A.X-4.0-Light-Q4_K_M.gguf`, context 4096으로 확인했습니다. Host에는 인증
없는 원격 포트를 열지 않고 SSH tunnel의 loopback 주소만 사용합니다. 정확한 모델
출처·해시·성능은
[모델 선정 문서](docs/model-selection.md)에 기록합니다.

한국어 STT는 whisper.cpp 서버로 동작합니다. 브라우저의 WebM/Opus는 웹 계층에서
16 kHz mono PCM WAV로 변환한 뒤 전사합니다. 실제 폰 녹음에서 이 변환이 없으면
whisper-server가 HTTP 400을 반환하므로 우회하지 않습니다.

한국어 TTS의 현재 stage 1은 Chatterbox Multilingual V3, stage 2는 학습한 Seed-VC입니다.
89.4분 학습 모델도 억양을 충분히 보존하지 못해 F0-conditioned 대체 경로를 검증 중이며,
천우의 청취 판정 전에는 운영 기본값을 변경하지 않습니다.

CPU 개발 환경을 기본값으로 두고, GPU와 Voice 장치는 명시적인 Compose overlay에서만
전달합니다. 선택 근거는 [ADR-0001](docs/adr/0001-docker-development-baseline.md)에
있습니다.

## 개발 원칙

- Docker-first: 애플리케이션과 의존성은 컨테이너에서 실행합니다.
- Local-first: 초기 대화 기능은 외부 상용 LLM API에 의존하지 않습니다.
- Shared Core: CLI와 Voice는 하나의 `CompanionCore`를 공유합니다.
- Coupled Reference: Human Reference의 context, 말투, 분위기와 음성을 함께 정렬합니다.
- Offline tests: 기본 테스트는 인터넷, GPU, 마이크, 모델 가중치, API 키 없이 실행됩니다.
- Privacy: 원본 영상·음성·자막, Reference corpus, 대화 DB, 모델 파일과 비밀 정보는 Git에 저장하지 않습니다.

Host와 Container의 책임, 조사된 환경 사양, 재확인해야 할 항목은
[환경 기준 문서](docs/environment.md)에 기록합니다.

## 작업 방식

모든 작업은 GitHub Issue로 시작하며, 기능·버그·조사·설계 결정을 동일한
상태 전이 형식으로 기록합니다. 원격 저장소에 push하기 전에는 README가 변경된
프로젝트 상태와 사용 방법을 정확히 반영하는지 확인합니다.

세부 제품·아키텍처·개발 규칙은 [AGENTS.md](AGENTS.md)를 기준으로 합니다.

## Companion Identity

Companion의 이름·역할·핵심 성격·가치관·관계 원칙·변경 불가 경계는 model prompt와
분리된 JSON Identity로 관리합니다. 파일은 개인 설정이므로 `data/` 아래에 두며 Git에
넣지 않습니다. 현재 초기 이름은 **겨울이**이며, 그 밖의 Core Persona는 사용자 승인
없이 자동 변경하지 않습니다. 형식은 [Identity 문서](docs/identity.md)를 따릅니다.

```bash
docker compose run --rm dev python -m companion.cli \
  --identity-path /workspace/data/identity.json --show-identity
```

## Explicit Memory lifecycle

일반 대화는 자동으로 영구 사실이 되지 않습니다. 관리 명령으로 추가한 항목은 처음
`candidate`가 되고, 검토 뒤 `approved`, 그 다음 `active`로 전이합니다.

```bash
docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite \
  --memory-add "천우가 명시적으로 기억해 달라고 한 내용"

docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --memory-approve <memory-id>

docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --memory-activate <memory-id>
```

기억의 내용을 바꿀 때는 기존 행을 덮어쓰지 않습니다. `--memory-replace`는 새
`candidate`를 만들고 기존 기억의 ID를 `supersedes`로 기록합니다. 새 항목이
`active`가 되는 순간에만 기존 active 항목을 `deprecated`로 전환하므로, 검토 중인
수정 때문에 현재 기억을 잃지 않습니다.

```bash
docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite \
  --memory-replace <memory-id> "수정할 내용"

docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --memory-deprecate <memory-id>

docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --list-memories
```

`deprecated`는 이력 보존을 위한 논리적 삭제입니다. 완전히 제거하려면 목록에서 ID를
확인한 뒤 아래처럼 명시적으로 삭제합니다. 다른 Memory가 해당 ID를 `supersedes`로
참조하면 이력 보호를 위해 삭제가 거부됩니다.

```bash
docker compose run --rm dev python -m companion.cli \
  --memory-db /workspace/data/memories.sqlite --memory-delete <memory-id>
```

직접 진술에서 같은 항목의 값이 바뀌거나 같은 대상의 선호가 명확히 뒤집히면 새 기억이
기존 active 기억을 `supersedes`하고 이전 항목은 `deprecated`가 됩니다. 모호한 의미
충돌을 LLM으로 판정하지는 않습니다. active Memory 중 현재 질문과 keyword가 겹치는
최대 3개를 전달하며, 명시적인 회상 질문에는 안정적인 최근 기억을 fallback으로 찾습니다.
candidate·approved·deprecated·rejected Memory는 전달되지 않습니다.

대화에서 `기억해` 또는 `기억해줘`로 시작하면 그 명령 자체를 사용자의 명시적 승인으로
본다. repository에는 `candidate → approved → active` 이력이 남고 즉시 다음 대화부터
사용된다. 이 경로는 정확한 명령에만 열려 있으며 자동 추론 결과에는 허용되지 않는다.

```bash
docker compose run --rm dev python -m companion.cli \
  --backend fake \
  --memory-db /workspace/data/memories.sqlite \
  --prompt "기억해. 나는 Python config를 선호해"
```

`나는 긴 설명을 싫어해`, `내 생일은 3월 12일이야`, `우리는 SQLite로 하기로 했어`처럼
안정적인 1인칭 진술은 별도 명령 없이도 사용자 원문 그대로 저장됩니다. CLI는 응답 아래에
자동 저장된 Memory ID를 표시하지만 이 안내 문구를 Voice로 읽지는 않습니다.

`지금`, `오늘`, `요즘`이 포함된 순간 상태, 물음표가 있는 질문, `것 같아` 같은 추측,
제3자에 대한 문장은 자동 기억하지 않습니다. 이 정책은 별도 LLM 호출이 없어 대답 속도를
늦추지 않으며, 모델의 해석을 사실로 승격하지도 않습니다. 더 넓은 맥락에서 추론하는
Reflection은 향후 candidate 제안으로만 추가합니다.

## Conversation continuity

CLI·phone web·Voice는 모두 `data/conversations.sqlite`, `memories.sqlite`,
`beliefs.sqlite`, `dialogue_state.sqlite`, `turn_understanding.sqlite`, `outcomes.sqlite`를
공유합니다.
최근 12개 밖의 대화도 현재 말과 관련 있으면 exact old turn을 최대 두 개 검색합니다.
오래된 문장을 새로운 사실로 요약하지 않습니다.

`내일 다시 보자`, `아직 고민 중이야`처럼 천우가 직접 미완성을 표시한 말은 Open Loop로
남습니다. 상태 확인과 종료는 다음처럼 합니다.

```bash
./winter chat --list-open-loops
./winter chat --resolve-open-loop <open-loop-id>
./winter chat --dismiss-open-loop <open-loop-id>
./winter chat --list-memories
```

각 사용자 턴은 실제 답변과 별개로 Shadow queue에 먼저 남습니다. 분석은 응답 지연을
피하기 위해 별도 명령으로 실행하고, 현재 결과는 Memory나 다음 답변에 주입하지 않습니다.

```bash
./winter chat --analyze-pending-turns --analysis-limit 10
./winter chat --list-turn-understanding
```

분석 실패는 `failed` 상태와 원인을 남기며 fake로 대체하지 않습니다. strict schema,
exact-evidence 규칙과 현재 한계는
[TurnUnderstanding Shadow Mode](docs/turn-understanding.md)에 기록했습니다.

이전 겨울이 답변과 바로 다음 천우 발화는 별도 `data/outcomes.sqlite`에 연결됩니다.
현재 Outcome 역시 Shadow Mode이며 실제 Memory나 다음 답변을 바꾸지 않습니다.

```bash
./winter chat --analyze-pending-outcomes --analysis-limit 10
./winter chat --list-outcomes
```

구조, 첫 Orin correction probe와 미통과 고도화 gate는
[OutcomeEvaluator 문서](docs/outcome-evaluator.md)에 기록했습니다.

Outcome DB v3는 Core가 실제 답변에 사용한 Memory provenance와 천우의 blind review를
서로 분리해 보존한다. Memory가 사용된
턴만 별도 relation evaluator가 확인·반박을 판정하고, Improvement candidate는 검증된
categorical failure에서 파생한다. 새 locked v2 15 scene을 두 번 실행한 결과는 18/30 runs,
194/212 checks, 7/7 synthetic gates이며 평균 5.19초다. Memory·Improvement precision/recall은
100%였지만 corrected/rejected와 continuation·ambiguous 세부 필드는 여전히 실패한다. 실제
Shadow 대화 30개 이상의 blind audit 전에는 답변·Memory·DialogueDirector에 연결하지 않는다.
실행법과 실패 분류는 [Outcome held-out 평가](docs/outcome-evaluation.md)를 따른다.

천우가 검토할 때는 모델 판단을 먼저 보지 않는다. 이전 질문, 실제 겨울이 답변과 다음
반응을 읽고 숫자로 평가하면 저장 후에만 겨울이 판단과의 차이를 보여준다. `s`는 해당
대화를 넘기고 `q`는 검토를 끝낸다. 이 명령은 LLM이 꺼져 있어도 실행된다.

```bash
./winter chat --review-outcomes
./winter chat --outcome-audit
```

`--outcome-audit`는 실제 검토 진행률을 `0/30`처럼 보여준다. 30개를 채우더라도 자동으로
기억이나 응답 정책을 바꾸지 않고, 천우가 결과를 보고 candidate 연결 여부를 결정한다.

대화 행동은 `DialogueDirector`가 선택하고, 회피성 의견·넓은 되묻기·잘못된 callback 같은
반복 실패만 `ResponseReviewer`가 한 번 재생성합니다. 일반 대화는 최대 2문장, 설명 요청은
최대 4문장입니다. 설계와 trade-off는
[ADR-0006](docs/adr/0006-shared-continuity-and-dialogue-control.md)에 기록했습니다.

실제 Orin 멀티세션 평가는 아래 명령으로 재현합니다.

```bash
PYTHONPATH=src:. python3.11 experiments/companion_dialogue_eval.py
```

## Winter Belief lifecycle

겨울이의 관점은 Core Persona나 천우에 대한 Memory와 분리합니다. 관점 후보에는 주제,
입장, 이유, 신뢰도와 최소 하나의 근거가 필요하며, 검토되어 `active`가 된 항목만 관련
대화에 사용됩니다.

```bash
docker compose run --rm dev python -m companion.cli \
  --belief-db /workspace/data/beliefs.sqlite \
  --belief-add "음성 개발 방향" "앞단 TTS 개선이 우선이다" \
  --belief-rationale "질문 억양 실패가 반복됐다" \
  --belief-confidence 0.78 \
  --belief-evidence voice-test-22 \
  --belief-evidence public-rvc-bakeoff

docker compose run --rm dev python -m companion.cli \
  --belief-db /workspace/data/beliefs.sqlite --belief-activate <belief-id>

docker compose run --rm dev python -m companion.cli \
  --belief-db /workspace/data/beliefs.sqlite --list-beliefs
```

기존 관점을 바꿀 때는 `--belief-revise <belief-id> "새 입장"`과 새 rationale·evidence를
함께 지정합니다. 새 candidate가 활성화되기 전에는 기존 관점이 유지됩니다. 자동
Reflection과 자동 활성화는 아직 제공하지 않으며, 설계 근거는
[ADR-0005](docs/adr/0005-persistent-winter-beliefs.md)에 기록합니다.

## 개발 환경 스펙

| 구분 | 기준 |
| --- | --- |
| Container Python | 3.11 |
| 기본 이미지 | `python:3.11-slim-bookworm` |
| 기본 실행 | CPU-only Docker Compose 서비스 `dev` |
| GPU | RTX 2060 6 GiB를 Host에서 확인; `compose.gpu.yaml`을 명시할 때만 전달 |
| Voice | PulseAudio/ALSA를 Host에서 확인; `compose.voice.yaml`과 Pulse socket을 명시할 때만 전달 |
| 모델·개인 데이터 | 이미지와 Git에서 제외, `data/`·`models/` bind mount로만 전달 |

## Docker 실행

기본 CPU 개발 셸을 build하고 Python runtime을 확인합니다.

```bash
docker compose build
docker compose run --rm dev python --version
```

`dev` 서비스는 Docker `local` logging driver를 사용하며, 로그는 10MB 파일 3개로
제한된다. 장시간 model probe가 Host 디스크를 과도하게 점유하지 않게 하기 위한 설정이다.

GPU 장치가 필요한 후속 adapter 작업에서만 GPU overlay를 명시합니다.

```bash
docker compose -f compose.yaml -f compose.gpu.yaml run --rm dev bash
```

Voice 작업에서는 먼저 `.env.example`을 `.env`로 복사하고 Host의 실제
PulseAudio socket 경로를 확인한 뒤 Voice overlay를 명시합니다.

```bash
docker compose -f compose.yaml -f compose.voice.yaml run --rm dev bash
```

기본 CLI는 의도적으로 fake adapter를 사용합니다. 이는 오프라인 개발과 테스트를
모델 서버의 상태에서 분리하기 위한 명시적 선택이며, `--backend local`이 실패해도
fake 응답으로 자동 전환하지 않습니다.

```bash
docker compose run --rm dev python -m companion.cli --backend fake --prompt "안녕"
```

실제 local LLM CLI는 Host의 runtime과 모델을 **read-only** mount한 `llm` 서비스와,
그 서비스에만 접속하는 `dev` CLI 컨테이너로 구성됩니다. 아래 변수 경로는 Host
경로이고, 컨테이너에서는 각각 `/runtime`, `/models`로만 보입니다.

```bash
export LLAMA_RUNTIME_DIR=/path/to/llama-b10276-parent
export LLM_MODEL_DIR=/path/to/gguf-directory
export LLM_MODEL_FILE=Qwen3-4B-Instruct-2507.Q4_K_M.gguf

docker compose -f compose.yaml -f compose.llm.yaml -f compose.gpu.yaml up -d llm
docker compose -f compose.yaml -f compose.llm.yaml -f compose.gpu.yaml run --rm dev \
  python -m companion.cli --backend local --prompt "한국어로 한 문장만 인사해줘"
docker compose -f compose.yaml -f compose.llm.yaml -f compose.gpu.yaml down
```

`LLAMA_RUNTIME_DIR`에는 그 아래에 `llama-b10276/llama-server`가 있는 디렉터리를
지정합니다. `llm`은 Host port를 공개하지 않으며 Compose 내부 `http://llm:8080`에서만
통신합니다.

대화를 프로그램 재시작 뒤에도 보존하려면 명시적으로 SQLite 경로를 지정합니다.
Docker 실행에서는 컨테이너 내부 `/tmp`가 실행마다 사라지므로, Host의 `./data`와
연결된 `/workspace/data` 아래를 사용합니다. `data/`와 `*.sqlite`는 Git에서 제외됩니다.
Host에서 DB를 직접 수정·삭제할 수 있도록, 처음 한 번 현재 사용자 UID/GID를 넘깁니다.

```bash
export LOCAL_UID=$(id -u)
export LOCAL_GID=$(id -g)

docker compose run --rm dev python -m companion.cli \
  --backend fake \
  --conversation-db /workspace/data/conversations.sqlite \
  --prompt "이 대화를 저장해줘"

docker compose run --rm dev python -m companion.cli \
  --backend fake \
  --conversation-db /workspace/data/conversations.sqlite \
  --show-history
```

현재 저장하는 것은 순서가 있는 원문 대화 기록뿐입니다. Local CLI는 기본적으로 최근
12개 메시지와 총 4,000자 안의 기록을 다음 모델 요청에 함께 넣습니다. 필요하면
`--context-max-messages`, `--context-max-characters`로 한도를 낮출 수 있습니다.
이는 당장 대화 흐름을 잇기 위한 context입니다. 장기 기억은 명시적 저장과 lifecycle을
통해서만 관리하며, 자동 영구 저장하지 않습니다.

테스트는 다음 명령으로 실행합니다.

```bash
docker compose run --rm dev pytest
```

lint와 type check는 다음 명령으로 실행합니다. 세 명령 모두 통과해야
`AGENTS.md`의 Definition of Done을 만족합니다.

```bash
docker compose run --rm dev ruff check .
docker compose run --rm dev mypy
```

규칙은 `pyproject.toml`의 `[tool.ruff]`와 `[tool.mypy]`에 있습니다. 완화한
항목에는 그 이유를 주석으로 적었습니다.

실제 LLM checkpoint는 Git과 Docker 이미지에 넣지 않습니다. 내려받은 파일의 경로를
명시해 Host에서 다음 수동 probe를 실행할 수 있습니다.

```bash
python3 experiments/local_llm_probe.py \
  --runtime-dir /path/to/llama-b10276 \
  --model-path /path/to/Qwen3-4B-Instruct-2507.Q4_K_M.gguf
```

이 probe는 Docker GPU를 사용하며, 파일을 read-only로 mount합니다. 모델 파일의
정확한 SHA-256과 검증 결과는 [모델 선정 문서](docs/model-selection.md)를 따릅니다.

한국어 STT CPU probe는 다음처럼 실행합니다.

```bash
python3 experiments/stt_probe.py \
  --model-path /path/to/ggml-small.bin \
  --audio-path /path/to/korean-fixture.flac
```

한국어 TTS probe는 별도 pinned Docker image를 build한 뒤 실행합니다.

```bash
docker build -f Dockerfile.melotts-probe -t winter-ai:melotts-probe .
python3 experiments/tts_probe.py \
  --cache-dir /path/outside/git/melotts-cache \
  --output-path /path/outside/git/melotts-korean.wav
```

Human Reference source의 메타데이터 preflight probe는 별도 pinned image로
실행합니다. media를 다운로드하지 않고 duration, audio format, 자막 언어만
조회합니다. 외장 storage mount와 `.env`의 `REFERENCE_STORAGE_ROOT`,
`REFERENCE_STORAGE_ID`가 필요합니다.

```bash
docker compose -f compose.reference-source.yaml run --rm reference-source-probe \
  --candidate-id <candidate-id> \
  --report-relative-path reports/<name>.json
```

실행 규약, 실패 정책, 설계 결정은
[source metadata preflight 문서](docs/reference-source-probe.md)를 따릅니다.

자막이 `raw_transcript`로 쓸 수 있는 품질인지 측정하는 probe는 같은 image에서
텍스트만 내려받아 실행합니다.

```bash
docker compose -f compose.reference-source.yaml run --rm reference-subtitle-probe \
  --candidate-id <candidate-id> \
  --report-relative-path reports/<name>.json \
  --duration <source-id>=<seconds>
```

측정 지표와 판정 기준은
[자막 품질 측정 문서](docs/reference-subtitle-quality.md)를 따릅니다.

승인된 source의 오디오를 수집할 때는 한 번에 하나씩, 재인코딩 없이 받습니다.

```bash
docker compose -f compose.reference-source.yaml run --rm reference-audio-ingest \\
  --candidate-id <candidate-id> \\
  --source-id <source-id> \\
  --report-relative-path reports/<name>.json \\
  --expected-duration-seconds <seconds>
```

수집 계약과 실패 정책은
[오디오 수집 문서](docs/reference-audio-ingest.md)를 따릅니다.

승인된 candidate와 source 등록은 전용 명령으로 합니다. manifest를 직접 편집하지
않습니다.

```bash
docker compose -f compose.reference-source.yaml run --rm reference-source-register --list
docker compose -f compose.reference-source.yaml run --rm reference-source-register \\
  --add-source <candidate-id> --source-uri <URI>
```

URI는 외장 private manifest에만 저장되며 stdout과 report에는 나오지 않습니다.

수집한 오디오의 전사 품질은 구간 단위로 측정합니다. whisper.cpp image에 ffmpeg가
포함돼 있어 자르기·리샘플·전사가 한 runtime에서 끝납니다.

```bash
PYTHONPATH=src python3 experiments/reference_transcription_probe.py \\
  --audio-path <외장>/raw/audio/<candidate>/<source>.webm \\
  --reference-vtt <외장>/raw/subtitles/<candidate>/<source>.ko-orig.vtt \\
  --model-path <모델>/ggml-small.bin \\
  --work-dir <외장>/derived/audio/stt-pilot \\
  --source-id <source-id> --start-seconds 600 --duration-seconds 300
```

측정 방법과 결과는
[STT 파일럿 문서](docs/reference-transcription-probe.md)를 따릅니다.

전사에서 말투 특성을 추출할 때는 대조 전사를 함께 측정합니다. 두 전사에 모두
나타나는 특성만 설계 근거로 씁니다.

```bash
PYTHONPATH=src python3 experiments/reference_speech_style.py \\
  --source-id <source-id> \\
  --transcript <외장>/derived/audio/stt-pilot/<source>-0-<duration>.vtt \\
  --secondary-transcript <외장>/raw/subtitles/<candidate>/<source>.ko-orig.vtt \\
  --report-path <외장>/reports/<name>.json
```

측정 항목과 결과는
[말투 특성 문서](docs/reference-speech-style.md)를 따릅니다.

대화형 source에서 Reference 발화만 골라내는 화자 검증은 별도 pinned image로
실행합니다. **현재 이 방법은 동작하지 않으며** 경위와 이유는
[화자 검증 문서](docs/speaker-verification.md)에 있습니다.

말투는 `configs/verbal_style/`의 Python profile로 관리합니다. 기본값은
`reference_broadcast`이며 Reference 측정에 근거합니다. 이전 정책은 `base`로 남겨
비교할 수 있습니다.

```bash
docker compose run --rm dev python -m companion.cli --backend fake \\
  --verbal-style reference_broadcast --prompt "안녕"
```

근거와 한계는 [말투 profile 문서](docs/verbal-style-profiles.md)를 따릅니다.

모델 선택값은 `configs/models/`의 Python profile로 관리합니다. 기본값은 `base`,
RTX 2060 6 GiB profile은 `rtx2060_6gb`(Vulkan GPU backend, 37 layers), CPU
profile은 `cpu`입니다.
