"""Offline coverage for private manifest registration.

Every URI here is an ``example.invalid`` placeholder. No real source appears.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from companion.reference_registry import (
    ReferenceRegistryError,
    next_source_id,
    public_registry_summary,
    register_candidate,
    register_source,
)
from companion.reference_storage import (
    ReferenceStorage,
    initialize_reference_storage,
    load_reference_storage,
)


def _storage(tmp_path: Path) -> ReferenceStorage:
    repository_root = tmp_path / "repo"
    external_root = tmp_path / "external"
    repository_root.mkdir()
    external_root.mkdir()
    return initialize_reference_storage(external_root, repository_root)


def test_registering_a_candidate_and_source_persists_to_disk(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    storage = register_candidate(storage, "candidate-001", "private/identity")
    storage = register_source(
        storage, "candidate-001", "source-001", "https://example.invalid/a"
    )

    reloaded = load_reference_storage(
        storage.root, tmp_path / "repo", expected_storage_id=storage.storage_id
    )
    source = reloaded.manifest.candidates[0].sources[0]
    assert source.source_id == "source-001"
    assert source.private_source_uri == "https://example.invalid/a"


def test_registering_a_second_source_preserves_the_first(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage = register_candidate(storage, "candidate-001", "private/identity")
    storage = register_source(
        storage, "candidate-001", "source-001", "https://example.invalid/a"
    )

    storage = register_source(
        storage, "candidate-001", "source-002", "https://example.invalid/b"
    )

    assert [source.source_id for source in storage.manifest.candidates[0].sources] == [
        "source-001",
        "source-002",
    ]


def test_duplicate_candidate_is_refused(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage = register_candidate(storage, "candidate-001", "private/identity")

    with pytest.raises(ReferenceRegistryError, match="already registered"):
        register_candidate(storage, "candidate-001", "private/other")


def test_duplicate_source_id_is_refused_across_candidates(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage = register_candidate(storage, "candidate-001", "private/one")
    storage = register_candidate(storage, "candidate-002", "private/two")
    storage = register_source(
        storage, "candidate-001", "source-001", "https://example.invalid/a"
    )

    # Reports and stored files are keyed by source ID alone, so the ID has to be
    # unique manifest-wide, not just inside one candidate.
    with pytest.raises(ReferenceRegistryError, match="source-001 is already registered"):
        register_source(
            storage, "candidate-002", "source-001", "https://example.invalid/b"
        )


def test_duplicate_uri_is_refused_and_names_the_existing_source(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage = register_candidate(storage, "candidate-001", "private/identity")
    storage = register_source(
        storage, "candidate-001", "source-001", "https://example.invalid/a"
    )

    with pytest.raises(ReferenceRegistryError, match="already registered as source-001"):
        register_source(
            storage, "candidate-001", "source-002", "https://example.invalid/a"
        )


def test_source_for_an_unknown_candidate_is_refused(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ReferenceRegistryError, match="is not registered"):
        register_source(
            storage, "candidate-999", "source-001", "https://example.invalid/a"
        )


def test_non_https_and_hostless_uris_are_refused(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage = register_candidate(storage, "candidate-001", "private/identity")

    with pytest.raises(ReferenceRegistryError, match="must use https"):
        register_source(storage, "candidate-001", "source-001", "http://example.invalid/a")
    with pytest.raises(ReferenceRegistryError, match="must use https"):
        register_source(storage, "candidate-001", "source-001", "file:///etc/passwd")
    with pytest.raises(ReferenceRegistryError, match="must name a host"):
        register_source(storage, "candidate-001", "source-001", "https:///a")


def test_blank_and_padded_identifiers_are_refused(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ReferenceRegistryError, match="non-empty"):
        register_candidate(storage, "   ", "private/identity")
    with pytest.raises(ReferenceRegistryError, match="surrounding whitespace"):
        register_candidate(storage, " candidate-001 ", "private/identity")


def test_next_source_id_skips_ids_already_used_anywhere(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage = register_candidate(storage, "candidate-001", "private/one")
    storage = register_candidate(storage, "candidate-002", "private/two")
    storage = register_source(
        storage, "candidate-001", "source-001", "https://example.invalid/a"
    )
    storage = register_source(
        storage, "candidate-002", "source-002", "https://example.invalid/b"
    )

    assert next_source_id(storage.manifest) == "source-003"


def test_registry_summary_never_exposes_uris_or_identity(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    storage = register_candidate(storage, "candidate-001", "private/identity-ref")
    storage = register_source(
        storage, "candidate-001", "source-001", "https://example.invalid/a"
    )

    serialized = str(public_registry_summary(storage.manifest))

    assert "example.invalid" not in serialized
    assert "identity-ref" not in serialized
    assert "source-001" in serialized
