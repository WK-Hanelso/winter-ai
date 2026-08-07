"""Private external storage contract for Human Reference data."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4


SCHEMA_VERSION = 1
SENTINEL_FILENAME = ".winter-reference-storage.json"
MANIFEST_FILENAME = "private-manifest.json"
LAYOUT_DIRECTORIES = (
    "raw",
    "raw/video",
    "raw/audio",
    "raw/subtitles",
    "derived",
    "derived/audio",
    "derived/transcripts",
    "derived/transcripts/raw",
    "derived/transcripts/normalized",
    "aligned",
    "aligned/scenes",
    "annotations",
    "splits",
    "artifacts",
    "artifacts/voice",
    "artifacts/models",
    "reports",
    "quarantine",
)
_ALLOWED_TOP_LEVEL_DIRECTORIES = {
    PurePosixPath(relative).parts[0] for relative in LAYOUT_DIRECTORIES
}


class ReferenceStorageError(RuntimeError):
    """Raised when private storage cannot be validated without ambiguity."""


@dataclass(frozen=True)
class PrivateSourceRef:
    source_id: str
    private_source_uri: str
    local_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class PrivateCandidateRef:
    candidate_id: str
    private_identity_ref: str
    sources: tuple[PrivateSourceRef, ...] = ()


@dataclass(frozen=True)
class ReferencePrivateManifest:
    schema_version: int
    storage_id: str
    candidates: tuple[PrivateCandidateRef, ...] = ()


@dataclass(frozen=True)
class ReferenceStorage:
    root: Path
    storage_id: str
    manifest: ReferencePrivateManifest

    def path(self, relative_path: str) -> Path:
        """Resolve a validated manifest path underneath this storage root."""
        normalized = PurePosixPath(validate_managed_relative_path(relative_path))
        return self.root.joinpath(*normalized.parts)


def validate_managed_relative_path(value: str) -> str:
    """Return a normalized POSIX path only when it stays in managed layout."""
    return _validate_relative_path(value).as_posix()


def validate_storage_root(root: Path, repository_root: Path) -> Path:
    """Validate an existing dedicated root without changing the filesystem."""
    if not root.is_absolute():
        raise ReferenceStorageError("reference storage root must be an explicit absolute path")
    if not repository_root.is_absolute():
        raise ReferenceStorageError("repository root must be an explicit absolute path")

    try:
        resolved_root = root.resolve(strict=True)
        resolved_repository = repository_root.resolve(strict=True)
    except OSError as error:
        raise ReferenceStorageError(
            "reference storage root and repository root must be an existing directory"
        ) from error

    if not resolved_root.is_dir() or not resolved_repository.is_dir():
        raise ReferenceStorageError(
            "reference storage root and repository root must be an existing directory"
        )
    if _contains(resolved_repository, resolved_root) or _contains(
        resolved_root, resolved_repository
    ):
        raise ReferenceStorageError(
            "reference storage root must not overlap the Git repository"
        )
    return resolved_root


def initialize_reference_storage(
    root: Path,
    repository_root: Path,
    *,
    storage_id: str | None = None,
) -> ReferenceStorage:
    """Initialize an empty dedicated root after explicit validation."""
    resolved_root = validate_storage_root(root, repository_root)
    if any(resolved_root.iterdir()):
        raise ReferenceStorageError(
            "reference storage root must be empty before explicit initialization"
        )

    chosen_storage_id = storage_id or str(uuid4())
    _validate_storage_id(chosen_storage_id)
    manifest = ReferencePrivateManifest(
        schema_version=SCHEMA_VERSION,
        storage_id=chosen_storage_id,
    )
    _validate_manifest(manifest)

    for relative in LAYOUT_DIRECTORIES:
        resolved_root.joinpath(*PurePosixPath(relative).parts).mkdir(
            mode=0o700,
            parents=False,
            exist_ok=False,
        )

    _write_json_exclusive(
        resolved_root / MANIFEST_FILENAME,
        _manifest_to_dict(manifest),
    )
    _write_json_exclusive(
        resolved_root / SENTINEL_FILENAME,
        {
            "manifest": MANIFEST_FILENAME,
            "schema_version": SCHEMA_VERSION,
            "storage_id": chosen_storage_id,
        },
    )
    return ReferenceStorage(resolved_root, chosen_storage_id, manifest)


def load_reference_storage(
    root: Path,
    repository_root: Path,
    *,
    expected_storage_id: str | None = None,
) -> ReferenceStorage:
    """Load only a complete storage root with matching sentinel and manifest."""
    resolved_root = validate_storage_root(root, repository_root)
    sentinel = _read_json_object(resolved_root / SENTINEL_FILENAME, "sentinel")

    schema_version = sentinel.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise ReferenceStorageError(
            f"unsupported reference storage schema version: {schema_version!r}"
        )
    if sentinel.get("manifest") != MANIFEST_FILENAME:
        raise ReferenceStorageError("reference storage sentinel has an invalid manifest path")

    storage_id = sentinel.get("storage_id")
    if not isinstance(storage_id, str):
        raise ReferenceStorageError("reference storage sentinel has an invalid storage ID")
    _validate_storage_id(storage_id)
    if expected_storage_id is not None and storage_id != expected_storage_id:
        raise ReferenceStorageError("reference storage ID does not match the expected drive")

    for relative in LAYOUT_DIRECTORIES:
        directory = resolved_root.joinpath(*PurePosixPath(relative).parts)
        if directory.is_symlink() or not directory.is_dir():
            raise ReferenceStorageError(
                f"reference storage layout is incomplete or unsafe: {relative}"
            )

    manifest = _manifest_from_dict(
        _read_json_object(resolved_root / MANIFEST_FILENAME, "private manifest")
    )
    _validate_manifest(manifest)
    if manifest.storage_id != storage_id:
        raise ReferenceStorageError("private manifest storage ID does not match sentinel")
    return ReferenceStorage(resolved_root, storage_id, manifest)


def save_private_manifest(
    storage: ReferenceStorage,
    manifest: ReferencePrivateManifest,
) -> ReferenceStorage:
    """Atomically replace a validated private manifest inside an initialized root."""
    sentinel = _read_json_object(storage.root / SENTINEL_FILENAME, "sentinel")
    if sentinel.get("schema_version") != SCHEMA_VERSION:
        raise ReferenceStorageError("reference storage sentinel schema version changed")
    if sentinel.get("manifest") != MANIFEST_FILENAME:
        raise ReferenceStorageError("reference storage sentinel manifest path changed")
    if sentinel.get("storage_id") != storage.storage_id:
        raise ReferenceStorageError("reference storage ID changed before manifest write")

    _validate_manifest(manifest)
    if manifest.schema_version != SCHEMA_VERSION:
        raise ReferenceStorageError(
            f"unsupported private manifest schema version: {manifest.schema_version!r}"
        )
    if manifest.storage_id != storage.storage_id:
        raise ReferenceStorageError("private manifest storage ID does not match storage")

    destination = storage.root / MANIFEST_FILENAME
    if destination.is_symlink() or not destination.is_file():
        raise ReferenceStorageError("private manifest destination is missing or unsafe")
    temporary = storage.root / f".{MANIFEST_FILENAME}.{uuid4().hex}.tmp"
    try:
        _write_json_exclusive(temporary, _manifest_to_dict(manifest))
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return ReferenceStorage(storage.root, storage.storage_id, manifest)


def _contains(parent: Path, child: Path) -> bool:
    return child == parent or parent in child.parents


def _validate_storage_id(storage_id: str) -> None:
    try:
        parsed = UUID(storage_id)
    except (ValueError, TypeError, AttributeError) as error:
        raise ReferenceStorageError("reference storage ID must be a UUID") from error
    if str(parsed) != storage_id:
        raise ReferenceStorageError("reference storage ID must use canonical UUID form")


def _validate_relative_path(value: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ReferenceStorageError("manifest path must be a safe root-relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ReferenceStorageError("manifest path must be a safe root-relative path")
    if not path.parts or path.parts[0] not in _ALLOWED_TOP_LEVEL_DIRECTORIES:
        raise ReferenceStorageError("manifest path must target a managed relative path")
    return path


def _validate_manifest(manifest: ReferencePrivateManifest) -> None:
    if manifest.schema_version != SCHEMA_VERSION:
        raise ReferenceStorageError(
            f"unsupported private manifest schema version: {manifest.schema_version!r}"
        )
    _validate_storage_id(manifest.storage_id)

    candidate_ids: set[str] = set()
    source_ids: set[str] = set()
    for candidate in manifest.candidates:
        _require_nonempty(candidate.candidate_id, "candidate ID")
        _require_nonempty(candidate.private_identity_ref, "private identity reference")
        if candidate.candidate_id in candidate_ids:
            raise ReferenceStorageError("private manifest contains a duplicate candidate ID")
        candidate_ids.add(candidate.candidate_id)
        for source in candidate.sources:
            _require_nonempty(source.source_id, "source ID")
            _require_nonempty(source.private_source_uri, "private source URI")
            if source.source_id in source_ids:
                raise ReferenceStorageError("private manifest contains a duplicate source ID")
            source_ids.add(source.source_id)
            for local_path in source.local_paths:
                _validate_relative_path(local_path)


def _require_nonempty(value: str, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceStorageError(f"{field_name} must be a non-empty string")


def _manifest_to_dict(manifest: ReferencePrivateManifest) -> dict[str, Any]:
    return {
        "schema_version": manifest.schema_version,
        "storage_id": manifest.storage_id,
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "private_identity_ref": candidate.private_identity_ref,
                "sources": [
                    {
                        "source_id": source.source_id,
                        "private_source_uri": source.private_source_uri,
                        "local_paths": list(source.local_paths),
                    }
                    for source in candidate.sources
                ],
            }
            for candidate in manifest.candidates
        ],
    }


def _manifest_from_dict(raw: dict[str, Any]) -> ReferencePrivateManifest:
    try:
        raw_candidates = raw["candidates"]
        if not isinstance(raw_candidates, list):
            raise TypeError("candidates must be a list")
        candidates = tuple(_candidate_from_dict(candidate) for candidate in raw_candidates)
        manifest = ReferencePrivateManifest(
            schema_version=raw["schema_version"],
            storage_id=raw["storage_id"],
            candidates=candidates,
        )
    except (KeyError, TypeError) as error:
        raise ReferenceStorageError(f"invalid private manifest: {error}") from error
    return manifest


def _candidate_from_dict(raw: Any) -> PrivateCandidateRef:
    if not isinstance(raw, dict):
        raise TypeError("candidate must be an object")
    raw_sources = raw["sources"]
    if not isinstance(raw_sources, list):
        raise TypeError("candidate sources must be a list")
    return PrivateCandidateRef(
        candidate_id=raw["candidate_id"],
        private_identity_ref=raw["private_identity_ref"],
        sources=tuple(_source_from_dict(source) for source in raw_sources),
    )


def _source_from_dict(raw: Any) -> PrivateSourceRef:
    if not isinstance(raw, dict):
        raise TypeError("source must be an object")
    local_paths = raw["local_paths"]
    if not isinstance(local_paths, list) or not all(
        isinstance(path, str) for path in local_paths
    ):
        raise TypeError("source local_paths must be a list of strings")
    return PrivateSourceRef(
        source_id=raw["source_id"],
        private_source_uri=raw["private_source_uri"],
        local_paths=tuple(local_paths),
    )


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise ReferenceStorageError(f"reference storage {label} is missing or unsafe")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ReferenceStorageError(f"could not read reference storage {label}: {error}") from error
    if not isinstance(raw, dict):
        raise ReferenceStorageError(f"reference storage {label} must be a JSON object")
    return raw


def _write_json_exclusive(path: Path, payload: dict[str, Any]) -> None:
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
    except OSError as error:
        raise ReferenceStorageError(f"could not create private storage file {path.name}") from error
