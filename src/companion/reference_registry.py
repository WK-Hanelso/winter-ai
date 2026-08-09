"""Register approved candidates and sources in the private manifest.

Registration used to happen by editing the manifest JSON by hand. That is not
reproducible and gives no guard against a duplicate ID, a malformed URI or an
accidentally dropped entry. This module makes registration an operation with
rules instead of an edit.

Every function returns a new storage handle; nothing mutates in place.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from companion.reference_storage import (
    PrivateCandidateRef,
    PrivateSourceRef,
    ReferencePrivateManifest,
    ReferenceStorage,
    save_private_manifest,
)

ALLOWED_URI_SCHEMES = frozenset({"https"})


class ReferenceRegistryError(RuntimeError):
    """Raised when a registration would leave the manifest ambiguous."""


def register_candidate(
    storage: ReferenceStorage,
    candidate_id: str,
    private_identity_ref: str,
) -> ReferenceStorage:
    """Add one candidate, refusing to redefine an existing ID."""
    _require_identifier(candidate_id, "candidate ID")
    _require_identifier(private_identity_ref, "private identity reference")
    if _find_candidate(storage.manifest, candidate_id) is not None:
        raise ReferenceRegistryError(f"candidate {candidate_id} is already registered")
    candidate = PrivateCandidateRef(
        candidate_id=candidate_id,
        private_identity_ref=private_identity_ref,
    )
    return _save(storage, storage.manifest.candidates + (candidate,))


def register_source(
    storage: ReferenceStorage,
    candidate_id: str,
    source_id: str,
    private_source_uri: str,
) -> ReferenceStorage:
    """Add one source to an existing candidate.

    Source IDs are unique across the whole manifest, not just within a
    candidate, because reports and stored files are keyed by source ID alone.
    """
    _require_identifier(source_id, "source ID")
    _validate_uri(private_source_uri)
    candidate = _find_candidate(storage.manifest, candidate_id)
    if candidate is None:
        raise ReferenceRegistryError(f"candidate {candidate_id} is not registered")
    for existing in storage.manifest.candidates:
        for source in existing.sources:
            if source.source_id == source_id:
                raise ReferenceRegistryError(f"source {source_id} is already registered")
            if source.private_source_uri == private_source_uri:
                raise ReferenceRegistryError(
                    f"that URI is already registered as {source.source_id}"
                )
    updated = PrivateCandidateRef(
        candidate_id=candidate.candidate_id,
        private_identity_ref=candidate.private_identity_ref,
        sources=candidate.sources
        + (
            PrivateSourceRef(
                source_id=source_id,
                private_source_uri=private_source_uri,
            ),
        ),
    )
    return _save(
        storage,
        tuple(
            updated if entry.candidate_id == candidate_id else entry
            for entry in storage.manifest.candidates
        ),
    )


def next_source_id(manifest: ReferencePrivateManifest, prefix: str = "source") -> str:
    """Return the next free ``<prefix>-NNN`` across the whole manifest."""
    used = {
        source.source_id
        for candidate in manifest.candidates
        for source in candidate.sources
    }
    index = 1
    while f"{prefix}-{index:03d}" in used:
        index += 1
    return f"{prefix}-{index:03d}"


def public_registry_summary(manifest: ReferencePrivateManifest) -> dict[str, Any]:
    """Return IDs and counts only. URIs and identity never appear here."""
    return {
        "schema_version": manifest.schema_version,
        "candidate_count": len(manifest.candidates),
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "source_count": len(candidate.sources),
                "sources": [
                    {
                        "source_id": source.source_id,
                        "local_path_count": len(source.local_paths),
                    }
                    for source in candidate.sources
                ],
            }
            for candidate in manifest.candidates
        ],
    }


def _find_candidate(
    manifest: ReferencePrivateManifest,
    candidate_id: str,
) -> PrivateCandidateRef | None:
    return next(
        (entry for entry in manifest.candidates if entry.candidate_id == candidate_id),
        None,
    )


def _save(
    storage: ReferenceStorage,
    candidates: tuple[PrivateCandidateRef, ...],
) -> ReferenceStorage:
    manifest = ReferencePrivateManifest(
        schema_version=storage.manifest.schema_version,
        storage_id=storage.manifest.storage_id,
        candidates=candidates,
    )
    return save_private_manifest(storage, manifest)


def _require_identifier(value: str, label: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceRegistryError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise ReferenceRegistryError(f"{label} must not have surrounding whitespace")


def _validate_uri(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ReferenceRegistryError("private source URI must be a non-empty string")
    parsed = urlparse(value)
    if parsed.scheme not in ALLOWED_URI_SCHEMES:
        raise ReferenceRegistryError("private source URI must use https")
    if not parsed.netloc:
        raise ReferenceRegistryError("private source URI must name a host")
