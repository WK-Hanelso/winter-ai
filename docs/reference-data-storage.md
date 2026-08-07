# Human Reference External Storage

Issue: [#71](https://github.com/WK-Hanelso/winter-ai/issues/71)

Human Reference의 실제 인물 정보, source URL, 영상, 음성, transcript, annotation과 파생
artifact는 Git 저장소가 아닌 사용자가 지정한 전용 외장 root에 보관한다. 이 문서는 해당
root를 안전하게 식별하고 초기화하는 계약이다.

## No implicit writes

프로그램은 기본 storage path를 갖지 않는다. `./data`, 현재 작업 디렉터리나 추정한 mount
경로로 자동 fallback하지 않는다. 다음 조건을 모두 만족한 뒤 명시적인 initialize를 호출할
때만 파일을 만든다.

- root와 repository root가 명시적인 절대경로다.
- 두 경로가 모두 이미 존재하는 directory다.
- root가 repository와 같거나, 내부이거나, 상위 directory가 아니다.
- root가 비어 있다.

외장 drive가 mount되지 않았을 때 mount point directory를 새로 만들어 Host disk에 데이터를
쓰는 일을 막기 위해 storage root 자체는 코드가 생성하지 않는다. 사용자가 외장 drive에
전용 빈 directory를 만든 뒤 그 경로를 전달해야 한다.

## Layout

초기화된 root는 아래 구조를 사용한다.

```text
<reference-root>/
├─ .winter-reference-storage.json
├─ private-manifest.json
├─ raw/
│  ├─ video/
│  ├─ audio/
│  └─ subtitles/
├─ derived/
│  ├─ audio/
│  └─ transcripts/
│     ├─ raw/
│     └─ normalized/
├─ aligned/scenes/
├─ annotations/
├─ splits/
├─ artifacts/
│  ├─ voice/
│  └─ models/
├─ reports/
└─ quarantine/
```

- `raw`: 원본 또는 원본에서 손실 없이 분리한 자료
- `derived`: 전처리 결과. 원본을 덮어쓰지 않는다.
- `aligned`: 이후 multimodal schema로 정렬된 scene
- `annotations`: 사람·자동 annotation과 confidence
- `splits`: train/validation/test membership manifest
- `artifacts`: Reference Voice 또는 model 실험 결과
- `reports`: 품질·수량·검증 결과
- `quarantine`: 화자, 출처, 품질 또는 중복이 확인되지 않은 자료

Scene record의 상세 필드는 다음 Multimodal Schema Issue에서 정의한다.

## Sentinel and drive identity

`.winter-reference-storage.json`은 root가 winter-ai용으로 명시적으로 초기화됐음을 나타낸다.

```json
{
  "manifest": "private-manifest.json",
  "schema_version": 1,
  "storage_id": "<uuid>"
}
```

mount path는 바뀔 수 있으므로 경로 문자열을 drive identity로 사용하지 않는다. 호출자가
기대하는 `storage_id`를 제공하면 load 시 sentinel과 일치해야 한다. schema version,
sentinel ID와 manifest ID가 다르면 다른 drive 또는 손상된 storage로 보고 실패한다.

기존 root를 재초기화하거나 sentinel을 조용히 덮어쓰지 않는다.

## Private manifest

Manifest는 사람이 편집하는 model config가 아니라 source가 늘면서 변경되는 private
runtime data다. 따라서 YAML/TOML config 대신 JSON으로 저장하고, Python dataclass와
validation code가 schema를 소유한다.

```text
ReferencePrivateManifest
├─ schema_version
├─ storage_id
└─ candidates
   ├─ candidate_id
   ├─ private_identity_ref
   └─ sources
      ├─ source_id
      ├─ private_source_uri
      └─ local_paths
```

`private_identity_ref`와 `private_source_uri`는 외장 manifest에만 존재한다. Git 문서, test와
public decision summary에는 익명 placeholder만 사용한다.

`local_paths`는 반드시 managed layout 아래의 POSIX relative path여야 한다. absolute path,
`..`, backslash와 알 수 없는 top-level directory는 거부한다. Manifest 저장은 임시 파일을
완성한 뒤 atomic replace한다.

## Python contract

현재는 CLI 명령을 제공하지 않는다. 후속 pipeline이 아래 API를 직접 사용한다.

```python
from pathlib import Path

from companion.reference_storage import (
    initialize_reference_storage,
    load_reference_storage,
)

repository_root = Path("/absolute/path/to/winter")
external_root = Path("/absolute/mounted/drive/winter-reference")

# 외장 root는 사용자가 미리 만든 빈 directory여야 한다.
storage = initialize_reference_storage(external_root, repository_root)

# 다음 실행에서는 저장해 둔 ID까지 확인한다.
same_storage = load_reference_storage(
    external_root,
    repository_root,
    expected_storage_id=storage.storage_id,
)
```

위 경로는 형식 예시이며 실제 Host에 생성한 경로가 아니다. 이 Issue의 테스트는 모두
pytest `tmp_path`만 사용한다.

## Failure behavior

다음 상황에서는 `ReferenceStorageError`를 발생시키고 다른 위치로 fallback하지 않는다.

- relative 또는 존재하지 않는 root
- repository와 겹치는 root
- 초기화 전부터 비어 있지 않은 root
- sentinel·manifest 또는 layout 누락
- symlink로 바뀐 managed layout
- 지원하지 않는 schema version
- 기대한 storage ID 불일치
- manifest의 중복 candidate/source ID
- root 밖을 가리키는 local path

실패 후 실제로 어떤 파일이 만들어졌는지 확인하지 않고 자동 삭제하거나 재초기화하지
않는다. 사용자가 storage 상태를 확인한 뒤 복구 절차를 선택한다.

## Deferred work

- 실제 외장 storage 경로 선택과 초기화
- scene-level multimodal schema
- checksum, deduplication과 provenance index
- backup 및 복구 정책
- source ingest CLI
- encryption at rest
