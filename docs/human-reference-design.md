# Human Reference Baseline Design

이 문서는 Milestone 4의 설계 기준이다. 실제 데이터 수집, Reference Human 선정과 모델
학습은 각각 후속 Issue에서 수행한다.

## Goal

실제 한 사람의 대화 맥락, 언어 행동, 분위기 변화와 목소리를 같은 시간축에 정렬하고,
CLI와 Voice가 공유할 수 있는 재현 가능한 Human Reference 기준선을 만든다.

Human Reference는 인간 전체의 정답이나 최종 겨울이가 아니다. 한 사람의 실제 관찰에서
출발하는 기준점이며, 이후 Winter Character는 이 기준과의 의도적인 차이로 설계한다.

## Current boundary

현재 `CompanionCore`는 하나의 response를 CLI와 Voice에 공유하는 올바른 interface 경계를
갖는다. 그러나 `VerbalStylePlanner`와 `ProsodyPlanner`는 coarse `dialogue_act`만 보고
별도로 동작한다. Milestone 4는 이를 바로 교체하기 전에 필요한 데이터와 평가 계약부터
확정한다.

```text
context / memory / relationship / user signal
                       │
                       ▼
              Appraisal / Dialogue State
                       │
                       ▼
               Joint Utterance Plan
               ├─ dialogue behavior
               ├─ lexical response
               ├─ verbal style
               └─ prosody / delivery events
                       │
                ┌──────┴──────┐
                ▼             ▼
               CLI           Voice
               text     text + local TTS
```

Voice adapter는 shared text를 다시 작성하지 않는다. CLI는 lexical response를 표시하고,
Voice는 같은 response의 pace, energy, pitch, pause, emphasis와 비언어 event를 실현한다.

## Corpus unit

하나의 scene record는 최소한 아래 정보를 연결해야 한다. 실제 schema와 필수/선택 필드는
multimodal schema Issue에서 확정한다.

```text
source_id / scene_id / source_date
speaker / interlocutor / relationship_context
scene_context / previous_utterance / next_utterance
start_time / end_time / edit_status
raw_transcript / normalized_transcript
audio_path / audio_quality / overlap
dialogue_act / atmosphere_before / atmosphere_after
speech_rate / pause / pitch / energy
laughter / sigh / hesitation / other_nonverbal_events
annotation_source / annotation_confidence
```

`raw_transcript`는 반복, 머뭇거림과 비문을 보존한다. `normalized_transcript`는 검색과
LLM 입력에 쓰되 원본을 덮어쓰지 않는다. 편집 여부와 speaker overlap을 기록해 측정할
수 없는 timing을 사실처럼 사용하지 않는다.

## One corpus, multiple views

원본을 특정 모델 입력 형식으로 축소하지 않고 다음 view를 파생한다.

| View | Input | Target | Purpose |
| --- | --- | --- | --- |
| Appraisal | context, relationship, previous turn | atmosphere, dialogue act | 상황 판단 검증 |
| Text behavior | context, state | lexical response | prompt/few-shot 또는 Qwen SFT |
| Delivery | context, text | prosody, pause, nonverbal events | Joint Planner 검증 |
| Voice | text, aligned clean audio | waveform/acoustic target | Reference Voice 평가·adaptation |
| Joint evaluation | 전체 scene context | text + delivery + audio | Human Reference 재현 평가 |

대화 행동에는 유용하지만 음질이 낮은 `Behavior Set`, 깨끗한 단일 화자 음성인 `Voice
Set`, 두 조건을 모두 만족하는 `Coupled Gold Set`을 구분한다.

## Storage boundary

원본 영상·음성·자막, aligned corpus, annotation, speaker reference와 학습 weight는 사용자가
지정한 외장 저장소에 둔다. 저장소에는 다음만 포함한다.

- schema와 manifest 형식
- processing 및 validation code
- 데이터 출처를 개인 원본 없이 추적할 수 있는 비민감 metadata 형식
- 공개 가능한 synthetic test fixture

코드가 특정 Host mount path를 가정하지 않도록 dataset root는 명시적인 Python config나
CLI 인자로 전달한다. 데이터가 없을 때 몰래 synthetic 또는 fake data로 전환하지 않는다.
외장 root의 초기화, sentinel, private manifest와 실패 정책은
[Human Reference External Storage](reference-data-storage.md)를 따른다.

## Dataset split and leakage

무작위 utterance 분할을 사용하지 않는다. 같은 영상과 사건이 train과 test에 동시에
들어가는 것을 막기 위해 source video, date와 interlocutor를 고려해 분할한다.

- `train`: reference example 검색 또는 학습
- `validation`: 설정과 checkpoint 선택
- `test`: 마지막까지 보지 않은 영상·시기·상대 조합

Reference의 시간에 따른 말투 변화를 섞지 않도록 첫 baseline은 명시적인 source 기간을
고정한다.

## Evaluation layers

1. **Ground truth:** 실제 scene의 원본 text와 audio
2. **Voice reproduction:** 같은 script를 Reference Voice로 합성해 voice gap 측정
3. **Behavior reproduction:** 새 context에서 생성한 text와 실제 다음 행동 비교
4. **Joint reproduction:** 생성 text와 Reference Voice 결합의 전체 인상 비교

단어 일치를 Human reproduction의 정답으로 삼지 않는다. dialogue act, 문장 길이,
직접성, 농담/진지함 전환, response timing과 delivery 범위를 함께 비교한다.

## Training decision gates

### Prompt/few-shot baseline first

검색된 유사 scene과 Reference profile만으로 held-out behavior를 재현한다. 평가 corpus 없이
prompt를 반복 수정하지 않는다.

### Qwen LoRA/SFT

상황 판단은 맞지만 Reference의 언어 행동과 말투가 반복적으로 흔들리고, 적절한 example이
있어도 동일 실패가 유지될 때만 선택한다. 처음부터 base model 전체를 재학습하지 않는다.

### Appraisal model

Qwen이 tone shift, 관계 맥락 또는 dialogue act를 반복적으로 오판하는 증거가 있을 때만
별도 model 또는 classifier를 검토한다. 이 구성요소가 이 프로젝트의 완전한 world model인
것처럼 과장하지 않는다.

### TTS adaptation

동일 script와 올바른 delivery plan에서도 음색, pace, pause 또는 표현이 Reference와
반복적으로 다를 때 선택한다. text behavior 실패를 TTS 학습으로 해결하지 않는다.

## Known limitations

- 공개 영상의 인물은 카메라용 persona를 수행할 수 있다.
- 편집은 침묵, 실패한 농담과 갈등 복구를 제거할 수 있다.
- 한 사람은 인간 전체가 아니라 하나의 기준점이다.
- 특정 상대에게만 나타난 행동을 stable personality로 잘못 해석할 수 있다.
- 자동 STT, diarization과 emotion label은 수동 검증 전에는 정답이 아니다.
- 현재 RTX 2060 6 GiB에서는 모든 모델을 동시에 학습·상주시킨다고 가정하지 않는다.

## Milestone sequence

세부 순서와 완료 조건은 [roadmap](roadmap.md)을 따른다. 다음 작업은 Reference Human을
바로 선택하는 것이 아니라, 먼저 [Reference Human 선정 기준](reference-human-selection.md)에
따라 같은 관찰 예산으로 후보의 데이터 적합성을 비교하는 것이다. 실제 인물 이름과 source
URL은 공개 GitHub가 아닌 외장 저장소의 private manifest에 둔다.
