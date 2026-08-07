# AGENTS.md

## 1. Project Mission

이 저장소의 목표는 단순한 챗봇이 아니라, 한 명의 사용자와 장기간 관계를 유지하는 **로컬 중심 Personal Companion**을 만드는 것이다.

이 Companion은 다음 특성을 가져야 한다.

- 프로그램을 종료하고 다시 실행해도 동일한 존재로 느껴져야 한다.
- 사용자와의 과거 대화, 결정, 선호, 프로젝트 상태를 지속적으로 기억해야 한다.
- 대화가 쌓일수록 사용자의 선호와 관계 맥락을 업데이트해야 한다.
- CLI와 Voice에서 동일한 정체성, 기억, 성격을 공유해야 한다.
- 목소리는 단순 TTS 출력이 아니라, 일관된 음색과 상황별 억양을 가진 Companion의 정체성 일부여야 한다.
- Claude, GPT, Gemini 등은 나중에 전문 작업을 위임하는 Worker로 연결한다.
- 초기 Companion의 대화와 기억 기능은 외부 상용 Foundation Model API에 의존하지 않는다.

핵심 제품 가설은 다음과 같다.

> 사전학습된 Local LLM, 외부화된 Identity, 구조화된 Memory, 사전학습된 TTS를 조합하면 사용자가 시간이 지나도 동일한 Companion과 대화하고 있다고 느낄 수 있다.

---

## 2. Fixed Requirements

아래 요구사항은 사용자가 명시적으로 변경하기 전까지 고정이다.

### 2.1 Interfaces

초기 버전부터 인터페이스는 반드시 두 개다.

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

### 2.2 Local-first

초기 개발 범위에서는 다음 외부 모델 API를 사용하지 않는다.

- OpenAI API
- Anthropic API
- Gemini API
- 기타 원격 대화형 LLM API

로컬에서 실행 가능한 사전학습 모델과 inference runtime을 사용한다.

로컬 프로세스 간 HTTP API 또는 localhost inference server는 허용한다. 금지되는 것은 외부 상용 모델 의존성이다.

### 2.3 Voice

Voice는 후순위 부가기능이 아니다. 초기 수직 단면에 반드시 포함한다.

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

다음 수직 단면을 가장 먼저 완성한다.

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

이 단계부터 Claude, GPT, Gemini 같은 외부 Worker 연결을 검토한다.

Companion Identity를 Worker에 넘기지 않는다. Worker는 전문 작업 결과만 반환하고, 최종 응답은 Companion이 재구성한다.

---

## 8. Current First Task

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
- 환경이나 데이터가 부족하면 정확히 무엇이 부족한지 말한다.
- 한 번에 지나치게 큰 범위를 구현하지 않는다.
- 구현 후 사용자가 직접 검증할 명령을 제공한다.

사용자의 선호 이름은 **천우**다.

---

## 15. First Codex Prompt

저장소를 연 뒤 Codex에게 아래 지시를 전달한다.

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
