# AGENTS.md

## 1. Project Mission

### 2026-09-10 우선순위 변경 — Text-first

사용자의 최신 결정에 따라 현재 제품 목표는 텍스트에서 (1) 일관된 겨울이와 기억의 연속성,
(2) 승인된 외부 Agent 활용, (3) 관계가 느껴지는 자연스러운 대화다. Voice 고도화와 실제
음성 검증은 후순위로 보류한다. 아래 기존 Voice-first 및 Level 3 선행 순서보다 이 결정이
우선한다. 기존 음성 코드·Reference 원본/정렬과 검증 기록은 보존한다.

Epic #127, T0 #128, 첫 코드 작업 T1.1 #129를 따른다. 기존 기록은 버리지 않고
[`docs/text-first-records-handoff.md`](docs/text-first-records-handoff.md)의 출처→후속 작업
연결을 유지한다. 코드와 기존 개인 DB를 다시 bootstrap하거나 초기화하지 않는다.
설계는 [`docs/text-first-refactoring-design.md`](docs/text-first-refactoring-design.md), 작업
순서는 [`docs/text-first-implementation-todo.md`](docs/text-first-implementation-todo.md)를 따른다.

이 저장소의 목표는 단순한 챗봇이 아니라, 한 명의 사용자와 장기간 관계를 유지하는 **로컬 중심 Personal Companion**을 만드는 것이다.

이 Companion은 다음 특성을 가져야 한다.

- 프로그램을 종료하고 다시 실행해도 동일한 존재로 느껴져야 한다.
- 사용자와의 과거 대화, 결정, 선호, 프로젝트 상태를 지속적으로 기억해야 한다.
- 대화가 쌓일수록 사용자의 선호와 관계 맥락을 업데이트해야 한다.
- CLI와 Voice에서 동일한 정체성, 기억, 성격을 공유해야 한다.
- 목소리는 단순 TTS 출력이 아니라, 일관된 음색과 상황별 억양을 가진 Companion의 정체성 일부여야 한다.
- Claude, GPT, Gemini 등은 나중에 전문 작업을 위임하는 Worker로 연결한다.
- 초기 Companion의 대화와 기억 기능은 외부 상용 Foundation Model API에 의존하지 않는다.

겨울이는 범용 Agent에 캐릭터를 씌운 제품이 아니다. **천우와 생활의 시간을 이어 가는
Companion**이 제품의 중심이고, 검색·개발·자동화 Worker는 그 관계를 지원하는 종속
capability다. 겨울이의 하루는 꾸며 낸 일기나 화면 밖 활동이 아니라 실제 대화, 결정,
평가, 실패, 관점 수정과 채택된 능력으로만 구성한다.

핵심 제품 가설은 다음과 같다.

> 사전학습된 Local LLM, 외부화된 Identity, 구조화된 Memory, 사전학습된 TTS를 조합하면 사용자가 시간이 지나도 동일한 Companion과 대화하고 있다고 느낄 수 있다.

Winter V1의 최상위 제품 목표는
[`docs/winter-v1-goal.md`](docs/winter-v1-goal.md)에 정의한다. V1은 천우와의 대화에서
의도·감정·기억·개선 신호를 이해하고 결과로 교정하며, 자신의 실제 능력과 한계를 토론한
뒤 천우가 승인한 변경만 격리된 Worker로 구현·검증·채택하는 **Level 3 공동 자기발전
Companion**을 목표로 한다.

이 목표에서 “자기발전”은 자유로운 자기 코드 수정이 아니다. 겨울이는 문제 인식, 토론,
제안과 최종 설명을 소유하고, Worker는 승인된 기술 scope만 구현한다. 구현 승인과 실제
capability 채택 승인은 분리하며, 테스트·audit·version·rollback 없는 변경은 겨울이의
능력으로 등록하지 않는다.

---

## 2. Fixed Requirements

아래 요구사항은 사용자가 명시적으로 변경하기 전까지 고정이다.

### 2.1 Interfaces

최종 인터페이스는 아래 두 개를 유지한다. 현재 Text-first 단계는 CLI가 수락 기준이며 Voice는
후속이다. 기존 공유 Core와 데이터 호환성은 보존한다.

1. **CLI**
   - 일반 텍스트 대화
   - 개발 및 디버깅
   - 기억 조회, 수정, 삭제
   - 내부 상태 확인
   - 음성 테스트와 benchmark 실행

2. **Voice**
   - 마이크 입력
   - STT
   - Companion Core 호출
   - TTS
   - 오디오 출력

CLI와 Voice는 별도의 Companion을 구현하면 안 된다. 두 인터페이스는 반드시 하나의 `CompanionCore`를 공유해야 한다.

```text
CLI ───┐
       ├── CompanionCore ── Identity / Memory / Dialogue State
Voice ─┘
```

CLI에서 저장된 기억은 Voice에서 사용되어야 하며, Voice에서 발생한 대화도 CLI에서 조회할 수 있어야 한다.

### 2.1.1 Zero-maintenance User Experience

실사용에서 천우가 해야 하는 일은 겨울이와 대화하는 것뿐이다. 기억 정리, 상태 최신화,
검색 index 갱신, 분석 실행을 위해 별도 CLI 명령이나 관리 화면 조작을 요구하지 않는다.

- 사용자 발화와 응답은 Shared Core가 내부 분석 queue에 자동 등록한다.
- 장기 분석은 응답 latency와 분리된 background 경로에서 실행한다.
- 확실한 직접 진술은 activation gate를 통과한 정책 안에서 내부 갱신할 수 있다.
- 애매하거나 충돌하는 정보는 명령형 review UI가 아니라 겨울이가 다음 대화에서 자연스럽게
  확인한다.
- 개발용 `overview`, `reflect`, `memories` 명령은 audit·debug·복구 도구이며 제품의 정상
  사용자 흐름이 아니다.
- 품질 gate가 실패한 기능은 자동 갱신하지 않고 Shadow로 관찰하지만, 이 안전 제약을
  사용자에게 반복 작업으로 전가하지 않는다.

### 2.1.2 Lived Continuity and Autonomous Belief

- CLI, Web와 Voice의 실제 사건은 하나의 `Winter Event` 시간축에 append-only로 남긴다.
- 대화 원문은 기존 대화 저장소에만 두고 Event에는 사건 종류, 근거 참조와 최소 metadata만
  기록한다.
- 충분한 독립 근거가 여러 날에 걸쳐 반복되고 이후 반응에서 실제로 지지된 낮은 위험의
  Winter Belief는 승인된 정책 범위에서 겨울이가 스스로 활성화·수정할 수 있다.
- 자동 판단은 confidence, evidence, 차단 이유와 revision history를 남기며 모니터링과
  rollback이 가능해야 한다.
- Core Identity, User Memory, Capability, permission/security, Worker 실행과 외부 write는
  이 자동 활성화 경로를 사용할 수 없다.
- 실제 사건이 없는 시간에 보낸 하루, 몸의 경험, 감정이나 활동을 만들지 않는다.

### 2.2 Local-first

초기 개발 범위에서는 다음 외부 모델 API를 사용하지 않는다.

- OpenAI API
- Anthropic API
- Gemini API
- 기타 원격 대화형 LLM API

로컬에서 실행 가능한 사전학습 모델과 inference runtime을 사용한다.

로컬 프로세스 간 HTTP API 또는 localhost inference server는 허용한다. 금지되는 것은 외부 상용 모델 의존성이다.

### 2.3 Voice

Voice는 겨울이의 장기 정체성 일부로 보존한다. 2026-09-10 사용자 결정에 따라 현재
고도화·실기 검증은 보류하며 텍스트 작업의 완료를 Voice 준비 여부에 걸지 않는다.

목소리는 다음 세 요소를 분리해서 다룬다.

- `Voice Identity`: 음색, 화자 일관성
- `Prosody`: 억양, 속도, pitch, energy, pause, 강조
- `Verbal Style`: 단어 선택, 문장 길이, 존댓말/반말, 대화 습관

이 세 요소는 Port와 데이터 필드에서는 분리하되, 발화를 계획할 때는 하나의 결합된
말하기 행동으로 다룬다. CLI와 Voice는 동일한 lexical response와 Companion 상태를
공유한다. Voice는 그 response의 prosody와 비언어 delivery를 TTS로 실현하며, 별도의
모델이 문장을 다시 작성하면 안 된다.

사전학습된 TTS weight를 사용한다. 처음부터 TTS 모델을 pre-train하지 않는다.

사용자가 명시적으로 선택한 특정 실존 인물은 개인용·비공개 `Human Reference` 기준선에
한해 대화 맥락, script, 음성과 delivery를 함께 분석하고 재현 평가에 사용할 수 있다.
원본과 파생 데이터는 로컬 외장 저장소에만 두며 Git이나 외부 서비스로 전송하지 않는다.
Human Reference는 최종 Companion 그 자체가 아니다. 최종 겨울이의 말투와 음성 정체성은
Reference 재현을 검증한 뒤 `Winter Delta`로 별도 결정한다.

### 2.4 Memory and Adaptation

초기 온라인 업데이트의 중심은 model weight가 아니라 다음 데이터다.

- 현재 대화 상태
- 장기 기억
- 사용자 선호
- 관계 맥락
- 프로젝트 상태
- 과거 결정과 후속 작업

매 대화마다 모델 weight를 수정하지 않는다.

Weight adaptation은 충분한 검증 데이터가 쌓인 뒤 별도 단계에서 수행한다.

Human Reference 작업에서도 학습을 미리 전제하지 않는다. 먼저 prompt/few-shot과 검색된
reference example로 held-out 장면을 재현하고, 반복되는 실패가 측정될 때만 Qwen LoRA/SFT,
별도 appraisal model 또는 TTS adaptation을 선택한다.

---

## 3. Core Architecture

권장 경계는 아래와 같다.

```text
┌─────────────────────────────────────────────────────┐
│                    Interfaces                       │
├───────────────────────┬─────────────────────────────┤
│ CLI                   │ Voice                       │
│ text input/output     │ mic → VAD/STT → audio out  │
└───────────┬───────────┴──────────────┬──────────────┘
            │                          │
            └──────────────┬───────────┘
                           ▼
                 ┌──────────────────┐
                 │ CompanionCore    │
                 ├──────────────────┤
                 │ Identity         │
                 │ Dialogue State   │
                 │ Context Builder  │
                 │ Memory Retrieval │
                 │ Local ChatModel  │
                 │ Response Planner │
                 └────────┬─────────┘
                          │
           ┌──────────────┼─────────────────┐
           ▼              ▼                 ▼
     Memory Store    Response Metadata   Event Logger
                          │
                          ▼
                 Prosody / TTS Adapter
```

### 3.1 Required Ports

구현체가 특정 모델이나 runtime에 강하게 결합되지 않도록 Port/Adapter 구조를 사용한다.

최소 Port:

- `ChatModel`
- `SpeechToText`
- `TextToSpeech`
- `AudioRecorder`
- `AudioPlayer`
- `ConversationRepository`
- `MemoryRepository`
- `EmbeddingModel` — 필요해질 때 추가
- `Clock`

예시:

```python
from typing import Protocol


class ChatModel(Protocol):
    def generate(self, request: "ChatRequest") -> "ChatResult":
        ...


class SpeechToText(Protocol):
    def transcribe(self, audio: "AudioInput") -> "Transcript":
        ...


class TextToSpeech(Protocol):
    def synthesize(self, request: "SpeechRequest") -> "AudioOutput":
        ...
```

Domain/Core 계층은 Ollama, llama.cpp, vLLM, Whisper, CosyVoice 같은 구체적인 라이브러리를 직접 import하면 안 된다.

---

## 4. Domain Concepts

### 4.1 Companion Identity

Companion Identity는 모델 prompt 한 줄이 아니라 별도의 영속 데이터다.

최소 구성:

- 이름
- 역할
- 핵심 성격
- 가치관
- 사용자와의 관계 원칙
- 대화 정책
- 변하지 않아야 하는 경계
- 현재 버전

Core Persona와 학습되는 Preference를 혼합하지 않는다.

```text
Core Persona
- 안정적
- 사용자의 명시적 승인 없이 자동 변경 금지

Adaptive Preference
- 대화 길이
- 기술 설명 깊이
- 친밀도
- 직접성
- 선호하는 반응 방식
```

### 4.2 Memory Types

모든 대화를 한 Vector DB에 넣는 방식으로 끝내지 않는다.

최소한 다음 기억 종류를 분리한다.

- `semantic`: 사용자에 대한 안정적인 사실
- `episodic`: 특정 시점의 사건과 대화
- `project`: 프로젝트 목표, 상태, blocker, next action
- `preference`: 사용자 선호
- `decision`: 사용자가 내린 결정과 근거
- `procedural`: Companion이 사용자를 지원하는 방식

각 기억에는 최소한 다음 메타데이터가 있어야 한다.

```text
id
kind
content
importance
confidence
status
source
created_at
updated_at
last_accessed_at
supersedes
```

권장 상태:

```text
candidate → approved → active → deprecated
                       └──────→ rejected
```

초기 버전에서는 자동 추출된 기억을 바로 `active`로 만들지 않는다. 명시적 사용자 진술이거나 검토된 기억만 활성화한다.

### 4.3 Companion Response

Core가 단순 문자열만 반환하지 않도록 한다.

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class ProsodyPlan:
    emotion: str
    pace: float
    energy: float
    pitch_offset: float
    pauses: tuple["Pause", ...] = ()
    emphasis: tuple["Emphasis", ...] = ()


@dataclass(frozen=True)
class CompanionResponse:
    text: str
    dialogue_act: str
    prosody: ProsodyPlan
```

초기 TTS Adapter가 모든 prosody 값을 지원하지 않더라도 이 계약은 유지한다.

---

## 5. Development Strategy

### 5.1 Vertical Slice First

첫 목표는 완성형 Agent가 아니다.

아래는 최초 Voice 수직 단면의 역사다. 현재는 Text-first TODO의 CLI 경로를 우선하며,
기존 모델·DB·실패 근거를 이어받아 기능별로 리팩터링한다.

```text
마이크 입력
→ STT
→ CompanionCore
→ Local LLM
→ structured response
→ TTS
→ 오디오 재생
```

동시에 CLI에서도 동일한 `CompanionCore`를 호출할 수 있어야 한다.

### 5.2 Initial Voice Interaction

첫 Voice 구현은 Push-to-talk로 시작한다.

```text
키 누름   → 녹음 시작
키 해제   → 녹음 종료
STT       → 텍스트
Core      → 응답
TTS       → 재생
```

초기부터 완전한 full-duplex 음성 대화를 구현하지 않는다.

진행 순서:

1. Push-to-talk
2. VAD 기반 speech segment
3. End-of-turn detection
4. Streaming STT/LLM/TTS
5. Barge-in
6. Full-duplex 최적화

### 5.3 Simple Before Distributed

초기에는 Python monorepo와 단일 프로세스 또는 최소한의 local subprocess를 우선한다.

초기부터 다음을 도입하지 않는다.

- Kubernetes
- 분산 microservices
- message broker
- 복잡한 multi-agent framework
- 클라우드 배포

단, 외부 라이브러리와 Core 사이의 interface는 분리한다.

### 5.4 Human Reference Before Original Character

겨울이의 Original Character를 사용자 직관만으로 바로 정의하지 않는다. 먼저 실제 한
사람의 관찰 가능한 대화와 음성을 결합한 Human Reference 기준선을 만들고, 보지 않은
장면에서 재현력을 확인한다.

```text
context / relationship / atmosphere
                 │
                 ▼
        Joint Utterance Plan
        ├─ lexical response
        ├─ dialogue behavior
        └─ prosody / delivery
                 │
          ┌──────┴──────┐
          ▼             ▼
         CLI           Voice
         text      text + local TTS
```

원본 corpus는 수집 시점에 특정 학습 형식으로 축소하지 않는다. 동일 시간축에 context,
interlocutor, raw/normalized transcript, atmosphere, audio, pause, pitch, energy와 비언어
event를 정렬하고, 이후 appraisal, text behavior, delivery, voice adaptation, joint
evaluation view를 파생한다.

---

## 6. Proposed Repository Layout

실제 구현에 맞게 변경할 수 있지만, 경계는 유지한다.

```text
.
├─ AGENTS.md
├─ README.md
├─ pyproject.toml
├─ configs/
│  ├─ companion.yaml
│  ├─ models.yaml
│  └─ voice.yaml
├─ docs/
│  ├─ architecture.md
│  ├─ memory-design.md
│  ├─ voice-design.md
│  ├─ evaluation.md
│  ├─ roadmap.md
│  └─ adr/
├─ experiments/
│  ├─ local_llm_probe.py
│  ├─ stt_probe.py
│  ├─ tts_probe.py
│  └─ voice_roundtrip.py
├─ src/
│  └─ companion/
│     ├─ cli/
│     ├─ voice/
│     ├─ core/
│     ├─ identity/
│     ├─ memory/
│     ├─ models/
│     ├─ adapters/
│     └─ infrastructure/
└─ tests/
   ├─ unit/
   ├─ integration/
   └─ fixtures/
```

---

## 7. Milestones

### Milestone 0 — Environment and Model Probes

목표:

- 실제 개발 장비 조사
- Local LLM, STT, TTS 각각 독립 실행
- 한국어 동작과 latency 측정

필수 산출물:

- `docs/environment.md`
- `docs/model-selection.md`
- 독립 probe scripts
- 재현 가능한 설치 명령
- 라이선스 확인 기록

완료 조건:

- 한국어 텍스트 → Local LLM 응답
- 한국어 음성 → STT 텍스트
- 한국어 텍스트 → TTS 오디오
- 세 구성요소 모두 외부 상용 AI API 없이 실행

### Milestone 1 — Shared CLI and Voice Core

목표:

- CLI와 Voice가 동일한 `CompanionCore` 사용
- Push-to-talk voice round trip
- 대화 메시지 SQLite 저장

완료 조건:

- CLI 대화 가능
- Voice 대화 가능
- 두 채널의 대화 기록이 하나의 저장소에서 조회됨
- 프로그램 재시작 후 기록 유지

### Milestone 2 — Identity and Persistent Memory

목표:

- Core Persona 영속화
- 명시적 기억 저장
- 기억 조회, 수정, 삭제
- 기억 후보 lifecycle

완료 조건:

- 사용자가 “기억해”라고 말한 내용을 저장
- CLI에서 memory inspect/edit/delete 가능
- 관련 기억이 다음 대화에 주입됨
- 모르는 기억을 지어내지 않음

### Milestone 3 — Voice Identity 0.1

목표:

- 일관된 speaker identity
- 최소한의 prosody profile
- 같은 문장을 여러 style로 합성
- A/B 평가 기록

완료 조건:

- 여러 발화가 동일 화자로 인식됨
- `neutral`, `calm`, `warm`, `serious` 중 최소 3개가 구분됨
- 한국어 고유명사와 기술용어 평가 포함

### Milestone 4 — Human Reference Baseline

목표:

- 한 명의 Reference Human을 대상으로 context, 말투, 분위기와 음성을 함께 정렬
- CLI와 Voice가 공유하는 Joint Utterance 기준선 정의
- 보지 않은 영상에서 text, voice, joint reproduction gap 측정
- 실제 실패 근거를 바탕으로 학습 방식 결정

완료 조건:

- 원본과 파생 corpus가 Git이 아닌 외장 저장소에 보관됨
- 소규모 corpus가 scene 단위로 정렬되고 annotation 신뢰도가 기록됨
- CLI와 Voice가 동일한 lexical response와 상태를 공유함
- 실제 원본, 동일 script 합성, 새로운 response 합성의 차이를 구분함
- prompt/few-shot, Qwen LoRA/SFT, appraisal model, TTS adaptation 중 필요한 다음 단계가 기록됨

### Milestone 5 — Winter Character Foundation

목표:

- 검증된 Human Reference와의 의도적인 `Winter Delta` 정의
- 말투, 음성, 관계 행동을 함께 변형한 Original Character 구축
- CLI와 Voice에서 동일한 겨울이 정체성 검증

완료 조건:

- Reference와 겨울이의 차이가 관찰 가능한 행동으로 기록됨
- 겨울이가 Reference를 그대로 복제하지 않고 독립된 정체성을 가짐
- 동일한 상황에서 CLI와 Voice의 의미·관계 행동이 일관됨

### Milestone 6 — Continual Preference Update

목표:

- 사용자의 응답 선호 추출
- 반복 관찰 기반 업데이트
- 변경 이력 유지

완료 조건:

- 단 한 번의 관찰로 안정 선호를 크게 변경하지 않음
- 명시적 사용자 선호는 높은 confidence로 반영
- 이전 값과 변경 이력 조회 가능

### Milestone 7 — Worker Integration

이 단계부터 Claude, GPT, Gemini 같은 외부 Worker 연결을 검토하고 V1 Level 3
공동 자기발전 cycle을 완성한다.

Companion Identity를 Worker에 넘기지 않는다. Worker는 전문 작업 결과만 반환하고, 최종 응답은 Companion이 재구성한다.

완료 조건:

- `SelfModel`, `CapabilityRegistry`, `ImprovementLedger`가 재시작 후 유지됨
- 천우가 승인하기 전에는 Worker job이 실행되지 않음
- Worker가 격리된 branch/worktree와 승인된 scope에서만 수정함
- 시스템이 Worker와 별도로 tests, lint, type, privacy와 scope를 검증함
- 천우의 채택 승인 뒤에만 capability가 `available`이 됨
- 채택된 변경을 rollback하고 이전 capability/version으로 복원할 수 있음
- 실제 작은 capability 하나가 대화 제안부터 구현·채택·재평가까지 전체 cycle을 완주함

---

## 8. Current Work Direction

현재 phase는 `text_first_foundation`이다. T0 #128에서 기준과 기록을 인계하고 T1.1 #129에서
공용 text runtime bootstrap 추출을 완료했다 (Docker offline 847 passed, Ruff/mypy 통과).
다음 active scope는 #130의 CharacterSnapshot과 ContextAssembler 추출이며 코드 구현 전이다.
Voice 실제 검증과 자기 코드 채택의 전체
cycle은 텍스트 경험의 선행조건이 아니다. 대화·기억·Identity·Reference 및 기존 코드/평가를
보존하고 신규 메모리 migration은 T3/T4 별도 Issue에서 원본 보존과 복구를 검증한다.

#126은 open/partial 보류다. background job/inbox, scheduler, typed read-only gateway는
T5의 재사용 근거로 남긴다. 아래 2026-08 기록의 "현재/다음"은 당시 상태이며 새 우선순위를
뜻하지 않는다. 실패한 품질 gate, 개인정보 경계, 승인 없는 write 금지는 계속 유효하다.

### 2026-08 Foundation Recovery 기록 — 보존, 실행 순서는 대체됨

Task 001과 초기 Milestone 기반은 완료되었다. 기존 저장소를 다시 bootstrap하지 않는다.
`Winter Day Reflection V0`의 구현 기반은 완료되었고 실제 3일 관찰 gate는 계속 유지한다.
사용자가 우선순위를 실제 전문 작업 능력으로 명시적으로 변경했으므로, 현재 최우선은
관계·기억뿐 아니라 작업 의도와 최종 판단도 겨울이가 소유한 채 외부 Worker를 지휘하는
`Winter-owned Work Orchestration` Shadow V0를 검증하는 것이다.

2026-08-19 전체 감사의 prerequisite 복구 순서에 따라 #119 `read-only SelfModel + persistent
CapabilityRegistry`, #120 `persistent conversational ImprovementLedger`와 #118 deferred queue
fairness의 구현·운영 검증을 완료했다. #118은 24시간 관찰 트랙에 남고 결과 품질 gate는 실패
상태다. #121 Fake Worker lifecycle, #122 isolated lifecycle bridge와 #123 project baseline·source
adoption crash recovery와 #124 exact lifecycle-to-source capability cycle의 Shadow 범위를
완료했다. #125 `sandbox adoption verifier and adversarial suite`도 실제 hostile fixture와
전체 회귀를 통과해 종료했다. 현재 단일 active scope는 Issue #126 `Winter-owned real
repository diagnosis canary`다. 첫 동기식 98.77초와 근거 밖 추천은 실패했다. 후속 Shadow는
single-job background coordinator와 evidence-backed claim 선택으로 접수 0.000초, 직후 일반
대화 2.138초, 접수 TTS 2.450초, 작업 전체 82.83초를 냈다. 대화 비차단과 새 사실 차단은
성공했지만 최종 결과는 다음 한 가지를 확정하지 못해 partial이다. 후속 SQLite job/inbox는
dead-process request와 미전달 결과를 복구하고 foreground-first scheduler는 작업 접수·phone
녹음 시작을 보호한다. 다만 실제 phone 동시 사용과 이미 시작한 generation preemption은
미검증이다. 2026-08-21 repository 기본 unit은 유지한 채 #126 user-service drop-in으로 actual
phone canary를 켰고 loopback/Tailscale HTTPS health를 확인했다. 실제 사용자 표본 전에는 기본
CLI/Web/service 계약으로 승격하지 않는다.
새 sandbox·ledger·registry·prompt 예외·동급 모델 순회, 실제 source write, Capability
activation과 일반 자동 위임은 금지한다.
아래 Work Orchestration 계약과 실패 기준선은 그대로 보존한다.

1. 현재 혼합 worktree의 사용자·Claude·Codex 변경을 보존한다.
2. 하나의 Winter Core Model이 `IntentBrief`와 `WinterDecision`을 모두 생성한다. 두 개의
   본체나 별도 최종 답변 모델로 나누지 않는다.
3. `IntentProposal → deterministic IntentBrief → WorkerTaskPacket → EvidenceReport →
   deterministic action을 포함한 WinterDecision` V2.1을 유지한다.
4. Worker에는 sanitized project text만 전달하고 Identity, Memory, Conversation, account
   credential, Human Reference와 음성을 전달하지 않는다.
5. 현재 운영 Codex V0의 explicit opt-in과 read-only 제한은 유지하며, 새 Shadow 판단을
   평가 전 실제 답변에 연결하지 않는다.
6. Qwen semantic-v2 12장면은 46/66·10.7초·11/12 schema, ambiguity 0/3,
   network scope 0/1, review assessment 3/6으로 반복 실패했다.
7. 천우의 Terms 동의 뒤 Gemma 3 12B도 같은 suite에서 40/66·15.2초·11/12 schema,
   ambiguity 0/3, read-only ready 0/2, review assessment 3/6으로 실패했다. 당시 14B/12B
   상대 순위는 Qwen > Gemma > A.X였지만 어느 모델도 activation하지 않는다.
8. Qwen3-30B-A3B Instruct 2507 Q4_K_M도 같은 suite에서 46/66·5.5초·12/12 schema였지만
   ambiguity 0/3, read-only ready 1/2, 올바른 network scope 0/1, review assessment 3/6으로
   실패했다. 실제 Core 7턴도 자동 14/14와 달리 사람 검토에서 화면 밖 신체 행동과 질문 대상
   변경이 드러나 운영 A.X보다 대화 교체 근거가 없었다. parameter 수나 schema 성공률만으로
   activation하지 않는다.
9. Gemma 3 27B IT Q4_K_M은 44/66·27.0초·11/12 schema, ambiguity 0/3, read-only
   ready 1/2, 올바른 network scope 0/1, review assessment 3/6으로 실패했다. 실제 Core
   대화도 Gemma role 교대 제약으로 HTTP 400이 났다. 같은 12~30B drop-in 반복은 중단하고
   새 모델 계열 또는 runtime이 기존 실패 축을 해결한다는 근거가 있을 때만 다음 probe를 한다.
10. 모델의 capability label을 실제 권한 강제로 간주하지 않는다. 목록·검색·텍스트 읽기만
   제공하는 typed project gateway와 Codex provider adapter를 Shadow 구현했다. 부모
   control-plane만 인증/API transport를 갖고 모델은 shell/web 없는 `env -i` MCP만 사용한다.
   synthetic live 1건만 통과했으므로 기존 Codex V0에는 아직 연결하지 않으며 path/network/
   write·prompt-injection 공격 suite 뒤에만 20 task Shadow로 이동한다.
11. write Worker는 구현 승인, 격리 worktree, 독립 검증, 별도 채택 승인과 rollback 전에는
   활성화하지 않는다.
12. isolated implementation proposal Shadow는 sanitized baseline/copy, exact path·line scope,
    network·HOME 없는 host unittest·Python 구조 검증과 source 불변 synthetic live를
    통과했다. exact patch와 승인 목적·실제 diff·host 검증에서 만든 Winter 설명을 SQLite에
    보존하고, 재시작 복원과 patch hash에 묶인 채택/재작업 결정 기록도 통과했다. 사용자-facing
    route에는 아직 연결하지 않는다. 채택 뒤 대상 before hash stale 차단, 중간 오류 전 파일
    복원, 적용 후 편집 보호와 재시작 rollback도 synthetic live로 통과했다. project 전체
    baseline, process crash 복구, 실제 repository 검증과 Capability Registry 등록은 여전히
    금지한다.
13. 계정 비밀 원문은 모델 Memory가 아니라 OS credential store/connector가 보관한다.
14. Day Reflection 실제 3일 사람 검토와 기존 Memory/Belief gate는 병행해 유지한다.
15. 운영 A.X의 첫 실제 저장소 Intent는 약 9.7초 뒤 paraphrase evidence, `account` resource
    오분류, 모순된 clarification과 추상 DoD로 gate에서 실패했다. Worker는 실행되지 않았고
    repository도 외부 전송되지 않았다. prompt 예외를 누적하거나 실제 route를 연결하지 말고,
    다음 Core 또는 단순화 계약을 같은 task로 먼저 검증한다.
16. 권한·resource·exact evidence를 정책으로 옮긴 A.X 간소 Intent도 6/6 schema·평균 4.9초지만
    ambiguity 0/2, 사람 검토 usable 1/6으로 실패했다. 분석을 구현으로 확대하고 실행 불가능한
    DoD를 만들며 누락된 referent를 추측했다. orchestration 코드를 더 늘리지 말고 새 Core
    후보가 이 6장면과 semantic-v2를 통과하는지 먼저 본다.
17. #126 실제 repository 진단은 invalid evidence linkage, truncated full review를 차단한 뒤
    세 번째에 98.77초로 완주했다. source는 불변이었지만 Local Core 최종 답변은 한 가지
    추천이라는 목표와 evidence 한계를 어겼다. 이를 activation 성공으로 표현하지 않는다.
18. 다음 실험은 최종 판단을 evidence-backed claim 선택으로 제한하는 작은 계약과 기존 실패
    축을 해결할 근거가 있는 Core/runtime의 비교다. Worker 보고서 단순 중계나 prompt 예외
    누적으로 우회하지 않는다.
19. #126 후속 실제 canary는 background 접수와 직후 일반 대화가 각각 0.000초·2.138초로
    비차단임을 보였다. 접수 TTS는 2.450초, 전체 task는 82.83초였다. 전문 작업 완료 시간을
    대화 첫 반응 SLA로 보고하지 않는다.
20. claim 선택 gate는 근거 밖 action 생성을 막았지만 결과가 partial이었다. SQLite job/inbox,
    foreground model 우선권과 actual phone first-play 검증 전에는 activation하지 않는다.
21. SQLite job/inbox는 실제 child-process crash 뒤 running request 재개와 미전달 결과 복구를
    통과했다. queued foreground 대화는 background보다 먼저 Local model에 들어가며 작업 접수,
    대화 종료와 phone 녹음 시작 뒤 6초 quiet window를 둔다. 이미 실행 중인 generation
    preemption, lease heartbeat와 actual phone 동시 사용은 미검증이므로 activation하지 않는다.
    실제 `:18080` 겹침 probe에서는 background를 먼저 제출했어도 0.2초 뒤 foreground가
    0.498초에 먼저 끝났고 background는 6.855초에 끝났다.
22. actual phone canary는 repository 기본 unit이 아닌
    `~/.config/systemd/user/winter-web.service.d/126-owned-work-canary.conf`로만 켠다. 실제 표본이
    실패하거나 rollback이 필요하면 drop-in을 제거하고 daemon-reload/restart한다.

TurnUnderstanding과 Reflection의 기존 품질 gate는 그대로 유지한다. 자동 Belief 정책이
있다는 이유만으로 검증되지 않은 LLM candidate를 active로 만들거나 write Worker를 자동
실행하지 않는다. 기존 TurnUnderstanding gate와 새 Belief safety gate를 모두 통과해야 한다.
전체 범위와 완료 조건은 `docs/winter-v1-goal.md`를 따른다.

## 8.1 Historical First Task — Completed

Codex가 저장소를 처음 열었을 때 다음 순서로 진행한다.

### Task 001 — Repository Bootstrap and Runtime Probe

1. 현재 저장소 상태를 확인한다.
2. 실제 장비 환경을 조사한다.
   - OS
   - Python
   - CPU
   - RAM
   - GPU
   - VRAM
   - CUDA / driver
   - microphone / audio backend
3. `pyproject.toml`과 최소 패키지 구조를 만든다.
4. 외부 API 없는 deterministic fake adapters를 먼저 만든다.
5. 다음 Port를 정의한다.
   - `ChatModel`
   - `SpeechToText`
   - `TextToSpeech`
   - `ConversationRepository`
6. Local LLM, STT, TTS 후보를 각각 최소 2개 조사한다.
7. 라이선스, 한국어 지원, VRAM, latency, streaming 지원을 비교한다.
8. 선택 근거를 `docs/model-selection.md`에 기록한다.
9. 실제 모델을 연결하기 전, fake adapters를 사용한 CLI/Voice orchestration test를 작성한다.
10. 모든 테스트를 실행하고 결과를 기록한다.

### Task 001 Acceptance Criteria

- `pytest`가 오프라인에서 실행된다.
- 기본 단위 테스트는 모델 checkpoint 다운로드 없이 통과한다.
- CLI와 Voice orchestration이 동일한 fake `CompanionCore`를 사용한다.
- 구체 모델을 교체해도 Core가 변경되지 않는 구조다.
- 환경과 모델 선택 근거가 문서화된다.
- 비밀키나 개인 데이터가 commit되지 않는다.

Task 001이 완료되기 전에는 실제 장기 기억, fine-tuning, 외부 Worker를 구현하지 않는다.

---

## 9. Agent Workflow

Codex는 한 번에 큰 기능 전체를 구현하지 않는다.

### Development Control Gate

모든 Agent는 코드·설정·운영 문서를 변경하기 전에 저장소 root의 `project-state.toml`을
읽고 `./winter project-check`를 실행한다.

- `current_phase`, `active_issue`, `active_work`에 포함되지 않은 기능을 확장하지 않는다.
- 우선순위를 바꾸려면 구현보다 먼저 사용자와 scope를 합의하고 GitHub Issue와
  `project-state.toml`을 갱신한다.
- `absent → contract → shadow → operational → validated` 단계를 건너뛰지 않는다.
- `operational`과 `validated` 승격에는 repository 안에서 다시 확인 가능한 evidence가
  있어야 한다.
- dependency가 `operational`이 아니면 종속 기능을 `operational`로 승격하지 않는다.
- 작업 종료 전 `./winter project-check`를 다시 실행하고 README, Roadmap, Maturity Ledger의
  명시적 상태가 같은지 확인한다.
- Gate 실패를 우회하거나 검사 코드를 약화해 기능을 통과시키지 않는다. 잘못된 상태·근거·
  dependency를 먼저 수정한다.

V0는 free-form 문서 의미와 기존 dirty worktree의 파일별 Issue scope를 자동 판정하지 못한다.
따라서 현재 혼합 baseline이 정리되기 전까지 Agent는 active Issue 외 파일 변경을 사용자에게
명시하고, 기존 변경과 자신의 변경을 구분해 보고한다.

작업 순서:

1. 관련 문서를 읽는다.
2. 현재 코드와 테스트를 확인한다.
3. 구현 전에 작은 계획을 작성한다.
4. 하나의 issue 범위만 구현한다.
5. 테스트를 먼저 추가하거나 acceptance test를 명확히 한다.
6. 가장 작은 완전한 변경을 만든다.
7. 테스트, lint, type check를 실행한다.
8. 문서와 ADR을 갱신한다.
9. 남은 한계와 다음 작업을 명시한다.

### README Synchronization

`README.md`는 프로젝트의 현재 사용 방법과 상태를 보여 주는 기준 문서다.

- 원격 저장소에 push하기 전, 해당 변경이 README의 목적, 구조, 실행 방법,
  설정, 검증 방법, 알려진 한계에 영향을 주는지 확인한다.
- 영향을 준다면 같은 변경 또는 PR 안에서 README를 함께 갱신한다.
- README 변경이 필요 없다고 판단한 경우에는 PR 설명에 그 이유를 간단히 기록한다.

### Maturity and Enhancement Tracking

구현 범위의 완료와 기능 전체의 완성도를 구분한다.

- 주요 기능을 추가하거나 동작·평가 결과가 바뀌면 `docs/v1-maturity-ledger.md`를 같은
  변경에서 갱신한다.
- 각 기능에 현재 가능한 범위, 검증 근거, 알려진 한계, activation gate와 다음 고도화
  항목을 남긴다.
- 단일 성공 사례나 테스트 통과만으로 기능을 완성형이라고 보고하지 않는다.
- 실패한 실험과 낮은 평가 결과도 삭제하거나 성공으로 표현하지 않고 baseline으로 남긴다.
- 사용자 보고에서 `범위 완료`, 현재 maturity와 남은 고도화를 구분한다.

### Branch Naming

```text
feat/<issue>-<short-name>
fix/<issue>-<short-name>
research/<issue>-<short-name>
chore/<issue>-<short-name>
```

### Commit Style

```text
feat: add shared companion core
fix: preserve conversation state across voice sessions
research: benchmark local Korean TTS candidates
test: add memory lifecycle coverage
docs: record local model selection decision
```

### Pull Request Requirements

PR에는 다음을 포함한다.

- 문제 정의
- 구현 범위
- 설계 선택과 이유
- 테스트 결과
- 실행 방법
- 알려진 한계
- 다음 작업

---

## 10. Testing Rules

### 10.1 Offline Defaults

기본 테스트는 다음 조건을 지켜야 한다.

- 인터넷 불필요
- 실제 모델 weight 불필요
- 마이크 불필요
- GPU 불필요
- 외부 API key 불필요

실제 모델 테스트는 별도 marker로 구분한다.

```text
unit
integration
model
voice
slow
manual
```

예시:

```bash
pytest -m "not model and not voice and not slow"
```

### 10.2 Required Tests

최소 테스트 범위:

- CLI와 Voice가 같은 Core를 사용
- 대화 저장과 재조회
- memory lifecycle
- memory update conflict
- Persona가 임의로 변경되지 않음
- unknown memory hallucination 방지
- structured response parsing
- STT/TTS adapter failure handling
- DB migration
- config validation

### 10.3 No Silent Fallback

실제 Local LLM 또는 TTS가 실패했을 때 몰래 fake adapter로 전환하지 않는다.

- 개발/테스트에서는 명시적으로 fake를 선택한다.
- 운영 모드에서 모델 실패 시 오류 상태를 사용자에게 알린다.
- 실행하지 않은 작업을 실행했다고 응답하지 않는다.

---

## 11. Security and Privacy

- 원본 영상·음성·자막, 대화 DB, Human Reference corpus, speaker reference, model checkpoint는 기본적으로 Git에서 제외한다.
- Human Reference 원본과 파생 데이터는 사용자가 지정한 로컬 외장 저장소에 보관한다.
- Git에는 schema, processing code, manifest 형식과 공개 가능한 synthetic fixture만 둔다.
- `.env`, API key, access token은 commit하지 않는다.
- Memory DB는 사용자가 조회, 수정, 삭제할 수 있어야 한다.
- 향후 Tool 실행 시 read/draft/write/destructive 권한을 분리한다.
- 외부로 데이터를 보내는 기능은 명시적 opt-in 없이 추가하지 않는다.

권장 `.gitignore` 대상:

```text
.env
.env.*
data/
models/
checkpoints/
voice_references/
human_reference/
datasets/
generated_audio/
*.db
*.sqlite
*.wav
*.mp3
*.flac
*.m4a
*.mp4
*.mkv
*.webm
```

샘플용 작은 공개 fixture만 `tests/fixtures/`에 둘 수 있다.

---

## 12. Prohibited Shortcuts

다음을 하지 않는다.

- Persona 전체를 하나의 거대한 system prompt에만 보관
- 모든 기억을 무분별하게 Vector DB에 저장
- 모든 대화를 자동 영구 기억으로 확정
- CLI용 Core와 Voice용 Core를 따로 구현
- 구체 runtime을 Domain 계층에서 직접 import
- 매 대화 후 model weight 업데이트
- 초기부터 multi-agent framework 도입
- 실제 모델 실패를 fake 응답으로 숨김
- 테스트 없이 prompt만 반복 수정
- 사용자 승인 없이 외부 서비스로 음성/대화 전송
- Human Reference의 script와 voice를 분리해 왜곡된 기준선을 만듦
- Human Reference 재현 결과를 검증 없이 최종 겨울이 정체성으로 확정

---

## 13. Definition of Done

기능은 다음 조건을 모두 만족해야 완료다.

- acceptance criteria 충족
- 테스트 통과
- type/lint 검사 통과
- 실패 경로 처리
- 사용자 데이터와 모델 파일이 Git에 포함되지 않음
- 실행 방법 문서화
- 설계 변경 시 ADR 작성
- 알려진 한계 기록
- CLI와 Voice 동작 일관성 확인

---

## 14. Communication Style for the User

사용자는 초보 개발자이지만 실제 AI/자율주행 시스템 경험이 있다.

Codex는 다음 방식으로 보고한다.

- 결론만 던지지 말고 핵심 원리를 설명한다.
- 실제 코드와 현업 관점의 trade-off를 함께 설명한다.
- 낙관적인 주장보다 검증 결과를 우선한다.
- 추측은 추측이라고 표시한다.
- 자동 테스트·서버 검증은 끝났지만 사용자가 직접 확인하지 않은 동작은
  `docs/v1-maturity-ledger.md`의 `사용자 관찰 대기`에 남기고 진행 보고마다 알린다. 비차단
  관찰을 사용자 작업으로 강제하지 않으며, 실제 선택이 필요한 항목과 구분한다.
- 환경이나 데이터가 부족하면 정확히 무엇이 부족한지 말한다.
- 한 번에 지나치게 큰 범위를 구현하지 않는다.
- 구현 후 사용자가 직접 검증할 명령을 제공한다.

사용자의 선호 이름은 **천우**다.

---

## 15. Historical First Codex Prompt — 재실행 금지

아래 최초 bootstrap prompt는 이력으로 보존한다. 새 세션은 §8과 project-state의 active
Issue를 읽으며 Task 001을 다시 실행하지 않는다.

```text
Read AGENTS.md completely before changing any file.

Start with Task 001 only.

Inspect the actual machine environment first. Then bootstrap the smallest
Python project that preserves the architectural boundaries in AGENTS.md.

Implement deterministic fake adapters and offline tests before integrating
any real model. CLI and Voice orchestration must share the same CompanionCore.

Do not use OpenAI, Anthropic, Gemini, or any remote LLM API.
Do not implement long-term autonomous learning or external worker agents yet.

Document the environment, candidate local runtimes, model/license trade-offs,
test results, and unresolved limitations. Stop after Task 001 acceptance
criteria are satisfied and report the exact commands I should run to verify it.
```
