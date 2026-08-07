# Human Reference Multimodal Scene Schema

Issue: [#73](https://github.com/WK-Hanelso/winter-ai/issues/73)

Human Reference의 말투와 목소리를 별도 샘플처럼 수집하지 않고, 하나의 대화 장면에서
맥락·전후 분위기·원문/정규화 문장·발화 전달을 같은 시간축에 정렬한다. 현재 schema
version은 `1`이며 Python dataclass와 validation이 계약을 소유한다.

## Record boundary

```text
SceneRecord
├─ anonymous identity: candidate_id / source_id / scene_id
├─ source-relative span and source_date
├─ context: setting / activity / relationship / preceding context
├─ atmosphere: before / after observation + source + confidence
├─ media assets: managed relative path only
├─ turns
│  ├─ speaker role / source-relative span
│  ├─ raw transcript / normalized transcript
│  ├─ dialogue act
│  ├─ optional measured delivery
│  └─ nonverbal events + evidence source
├─ quality: edit / scriptedness / audio / overlap
└─ task annotations: target / label / evidence span / source / confidence
```

장면 JSON에는 실명, 공개·비공개 source URL과 Host 절대경로를 넣지 않는다. 실제 인물과
source locator의 연결은 외장 저장소의 `private-manifest.json`에만 둔다. media path는
초기화된 외장 storage layout 아래의 POSIX relative path만 허용한다.

## Time and transcript semantics

- 모든 `start_ms`와 `end_ms`는 잘라낸 파일 기준이 아니라 원본 source 시작점 기준이다.
- scene 안에 turn, turn 안에 해당 nonverbal event가 완전히 포함되어야 한다.
- annotation evidence도 scene 안의 구체적인 구간을 가리킨다.
- `raw_transcript`는 머뭇거림, 반복과 비문을 보존한다.
- `normalized_transcript`는 검색·LLM 입력용이며 raw를 덮어쓰지 않는다.
- 측정하지 않은 pitch, energy, pause와 speech rate는 추정값 `0`이 아니라 `null`이다.

이 규칙은 편집된 영상이나 자동 분석 결과를 실제 자연스러운 timing처럼 오인하는 것을
막는다. `edit_status`, `scriptedness`, `audio_quality`, `overlap_ratio`도 record마다 남긴다.

## Observation versus inference

분위기, dialogue act, nonverbal event와 generic annotation은 모두
`annotation_source`와 `confidence`를 가진다.

- `human`: 사람이 직접 검토한 판단
- `automatic`: STT·분류기 등 자동 도구 결과
- `derived`: 다른 측정값에서 계산한 결과
- `unknown`: 기존 자료의 근거를 확인할 수 없음

자동 label은 source가 `automatic`인 관찰 후보일 뿐 정답으로 승격되지 않는다. `before`와
`after` 분위기를 분리해, 따뜻한 발화 직후 날카로운 발화처럼 대화 방향이 급변한 사례도
한 개의 고정 emotion label로 뭉개지 않고 장면 전이로 평가할 수 있다.

## Shared CLI and Voice views

하나의 scene에서 목적별 view를 파생한다.

| View | 사용 필드 | 검증 목적 |
| --- | --- | --- |
| CLI behavior | context, atmosphere, raw/normalized text, dialogue act | 같은 상황에서 말투와 대화 행동 재현 |
| Delivery plan | context, text, delivery, nonverbal event | text와 어울리는 속도·pause·표현 선택 |
| Voice | normalized text, aligned media, measured delivery | 같은 문장의 음색·운율 재현 |
| Joint | scene 전체 | 말의 내용과 소리가 한 사람처럼 결합되는지 평가 |

CLI와 Voice는 서로 다른 답변 corpus를 갖지 않는다. shared lexical response는 같고,
Voice만 동일 turn의 delivery와 audio target을 추가로 사용한다.

## Python API

```python
from pathlib import Path

from companion.reference_scene import load_scene_record

scene = load_scene_record(Path("/external-root/aligned/scenes/scene-001.json"))
print(scene.turns[0].normalized_transcript)
```

`scene_record_from_dict`와 `scene_record_to_dict`는 strict round-trip을 제공한다. 알 수 없는
필드, 잘못된 enum/confidence/date, 중복 ID, 장면 밖 시간, 존재하지 않는 media/annotation
참조를 거부한다. `dump_scene_record`는 기존 파일을 덮어쓰지 않고 mode `0600`으로 새 파일만
생성한다.

Git의 [합성 fixture](../tests/fixtures/reference_scene_v1.json)는 실제 사람, 음성 또는 source
정보가 없는 한국어 예시다. 기본 테스트는 이 fixture만 사용하므로 외장하드, 인터넷,
모델, GPU와 마이크가 없어도 실행된다.

## Deferred work

- 실제 후보와 source를 승인한 뒤 외장 storage 초기화
- source ingest, STT, diarization과 장면 자동 분할
- annotation 도구와 사람 검토 workflow
- checksum 생성, 중복 탐지와 provenance index
- source 단위 train/validation/test split
- 각 view를 Qwen/TTS 입력으로 변환하는 exporter

Schema가 존재한다는 것은 실제 Human Reference 데이터가 확보됐거나 학습이 시작됐다는
뜻이 아니다. 다음 probe에서 작은 source subset의 usable 비율과 수동 정렬 비용을 먼저
측정한다.
