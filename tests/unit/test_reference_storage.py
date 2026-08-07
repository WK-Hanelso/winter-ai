import json
from pathlib import Path
import stat

import pytest

from companion.reference_storage import (
    LAYOUT_DIRECTORIES,
    MANIFEST_FILENAME,
    SCHEMA_VERSION,
    SENTINEL_FILENAME,
    PrivateCandidateRef,
    PrivateSourceRef,
    ReferencePrivateManifest,
    ReferenceStorageError,
    initialize_reference_storage,
    load_reference_storage,
    save_private_manifest,
    validate_storage_root,
)


def _roots(tmp_path: Path) -> tuple[Path, Path]:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    external_root = tmp_path / "external-reference"
    external_root.mkdir()
    return repository_root, external_root


def test_validation_rejects_relative_root_without_writing(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    relative_root = Path("reference-data")

    with pytest.raises(ReferenceStorageError, match="absolute"):
        validate_storage_root(relative_root, repository_root)

    assert not (tmp_path / relative_root).exists()


def test_validation_rejects_repository_overlap(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    inside_repository = repository_root / "private-reference"
    inside_repository.mkdir()

    with pytest.raises(ReferenceStorageError, match="overlap"):
        validate_storage_root(inside_repository, repository_root)

    with pytest.raises(ReferenceStorageError, match="overlap"):
        validate_storage_root(tmp_path, repository_root)


def test_validation_requires_existing_directory_without_creating_it(tmp_path: Path) -> None:
    repository_root = tmp_path / "repository"
    repository_root.mkdir()
    missing_root = tmp_path / "missing-external-root"

    with pytest.raises(ReferenceStorageError, match="existing directory"):
        validate_storage_root(missing_root, repository_root)

    assert not missing_root.exists()


def test_validation_of_valid_root_does_not_initialize_it(tmp_path: Path) -> None:
    repository_root, external_root = _roots(tmp_path)

    assert validate_storage_root(external_root, repository_root) == external_root.resolve()
    assert list(external_root.iterdir()) == []


def test_initialize_creates_versioned_private_layout(tmp_path: Path) -> None:
    repository_root, external_root = _roots(tmp_path)

    storage = initialize_reference_storage(external_root, repository_root)

    assert storage.root == external_root.resolve()
    assert storage.manifest.schema_version == SCHEMA_VERSION
    assert storage.manifest.storage_id == storage.storage_id
    assert storage.manifest.candidates == ()
    assert (external_root / SENTINEL_FILENAME).is_file()
    assert (external_root / MANIFEST_FILENAME).is_file()
    assert all((external_root / relative).is_dir() for relative in LAYOUT_DIRECTORIES)
    assert all(
        stat.S_IMODE((external_root / relative).stat().st_mode) & 0o077 == 0
        for relative in LAYOUT_DIRECTORIES
    )
    assert all(
        stat.S_IMODE((external_root / filename).stat().st_mode) & 0o077 == 0
        for filename in (SENTINEL_FILENAME, MANIFEST_FILENAME)
    )

    sentinel = json.loads((external_root / SENTINEL_FILENAME).read_text(encoding="utf-8"))
    assert sentinel == {
        "manifest": MANIFEST_FILENAME,
        "schema_version": SCHEMA_VERSION,
        "storage_id": storage.storage_id,
    }


def test_initialize_refuses_to_overwrite_existing_storage(tmp_path: Path) -> None:
    repository_root, external_root = _roots(tmp_path)
    first = initialize_reference_storage(external_root, repository_root)

    with pytest.raises(ReferenceStorageError, match="empty"):
        initialize_reference_storage(external_root, repository_root)

    loaded = load_reference_storage(
        external_root,
        repository_root,
        expected_storage_id=first.storage_id,
    )
    assert loaded.storage_id == first.storage_id


def test_private_manifest_round_trip_uses_only_root_relative_paths(tmp_path: Path) -> None:
    repository_root, external_root = _roots(tmp_path)
    storage = initialize_reference_storage(external_root, repository_root)
    manifest = ReferencePrivateManifest(
        schema_version=SCHEMA_VERSION,
        storage_id=storage.storage_id,
        candidates=(
            PrivateCandidateRef(
                candidate_id="candidate-001",
                private_identity_ref="private-person-001",
                sources=(
                    PrivateSourceRef(
                        source_id="source-001",
                        private_source_uri="private://source/001",
                        local_paths=("raw/video/source-001.webm",),
                    ),
                ),
            ),
        ),
    )

    saved = save_private_manifest(storage, manifest)
    loaded = load_reference_storage(
        external_root,
        repository_root,
        expected_storage_id=storage.storage_id,
    )

    assert saved.manifest == manifest
    assert loaded.manifest == manifest


@pytest.mark.parametrize("unsafe_path", ("../escape.webm", "/tmp/escape.webm"))
def test_private_manifest_rejects_unsafe_paths(
    tmp_path: Path, unsafe_path: str
) -> None:
    repository_root, external_root = _roots(tmp_path)
    storage = initialize_reference_storage(external_root, repository_root)
    manifest = ReferencePrivateManifest(
        schema_version=SCHEMA_VERSION,
        storage_id=storage.storage_id,
        candidates=(
            PrivateCandidateRef(
                candidate_id="candidate-001",
                private_identity_ref="private-person-001",
                sources=(
                    PrivateSourceRef(
                        source_id="source-001",
                        private_source_uri="private://source/001",
                        local_paths=(unsafe_path,),
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(ReferenceStorageError, match="relative path"):
        save_private_manifest(storage, manifest)

    assert load_reference_storage(external_root, repository_root).manifest.candidates == ()


def test_load_rejects_wrong_storage_id_and_schema_version(tmp_path: Path) -> None:
    repository_root, external_root = _roots(tmp_path)
    storage = initialize_reference_storage(external_root, repository_root)

    with pytest.raises(ReferenceStorageError, match="storage ID"):
        load_reference_storage(
            external_root,
            repository_root,
            expected_storage_id="00000000-0000-0000-0000-000000000000",
        )

    sentinel_path = external_root / SENTINEL_FILENAME
    sentinel = json.loads(sentinel_path.read_text(encoding="utf-8"))
    sentinel["schema_version"] = SCHEMA_VERSION + 1
    sentinel_path.write_text(json.dumps(sentinel), encoding="utf-8")

    with pytest.raises(ReferenceStorageError, match="schema version"):
        load_reference_storage(external_root, repository_root)


def test_load_rejects_manifest_from_another_storage(tmp_path: Path) -> None:
    repository_root, external_root = _roots(tmp_path)
    initialize_reference_storage(external_root, repository_root)
    manifest_path = external_root / MANIFEST_FILENAME
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["storage_id"] = "00000000-0000-0000-0000-000000000000"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ReferenceStorageError, match="does not match sentinel"):
        load_reference_storage(external_root, repository_root)


def test_load_rejects_layout_symlink_that_can_escape_root(tmp_path: Path) -> None:
    repository_root, external_root = _roots(tmp_path)
    initialize_reference_storage(external_root, repository_root)
    outside = tmp_path / "outside"
    outside.mkdir()
    managed_directory = external_root / "raw" / "video"
    managed_directory.rmdir()
    managed_directory.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ReferenceStorageError, match="incomplete or unsafe"):
        load_reference_storage(external_root, repository_root)


def test_manifest_write_rechecks_drive_identity(tmp_path: Path) -> None:
    repository_root, external_root = _roots(tmp_path)
    storage = initialize_reference_storage(external_root, repository_root)
    sentinel_path = external_root / SENTINEL_FILENAME
    sentinel = json.loads(sentinel_path.read_text(encoding="utf-8"))
    sentinel["storage_id"] = "00000000-0000-0000-0000-000000000000"
    sentinel_path.write_text(json.dumps(sentinel), encoding="utf-8")

    with pytest.raises(ReferenceStorageError, match="ID changed"):
        save_private_manifest(storage, storage.manifest)

    manifest = json.loads(
        (external_root / MANIFEST_FILENAME).read_text(encoding="utf-8")
    )
    assert manifest["storage_id"] == storage.storage_id
