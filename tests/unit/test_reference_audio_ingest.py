"""Offline coverage for the audio pilot ingest.

The downloader is faked. No test contacts a network or names a real source.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from companion.reference_audio_ingest import (
    AudioIngestRecord,
    DownloadedAudio,
    ReferenceAudioIngestError,
    dump_ingest_record,
    ingest_source_audio,
    public_ingest_summary,
    sha256_of,
)
from companion.reference_storage import (
    PrivateCandidateRef,
    PrivateSourceRef,
    ReferencePrivateManifest,
    ReferenceStorage,
    initialize_reference_storage,
    save_private_manifest,
)

_AUDIO_BYTES = b"synthetic-audio-payload" * 2000


def _storage(tmp_path: Path) -> ReferenceStorage:
    repository_root = tmp_path / "repo"
    external_root = tmp_path / "external"
    repository_root.mkdir()
    external_root.mkdir()
    storage = initialize_reference_storage(external_root, repository_root)
    manifest = ReferencePrivateManifest(
        schema_version=storage.manifest.schema_version,
        storage_id=storage.storage_id,
        candidates=(
            PrivateCandidateRef(
                candidate_id="candidate-001",
                private_identity_ref="private/identity",
                sources=(
                    PrivateSourceRef(
                        source_id="source-003",
                        private_source_uri="https://example.invalid/source-003",
                    ),
                ),
            ),
        ),
    )
    return save_private_manifest(storage, manifest)


class _FakeDownloader:
    def __init__(
        self,
        *,
        payload: bytes = _AUDIO_BYTES,
        extension: str = "m4a",
        audio_codec: str = "mp4a.40.2",
        extra_files: tuple[str, ...] = (),
    ) -> None:
        self._payload = payload
        self._extension = extension
        self._audio_codec = audio_codec
        self._extra_files = extra_files
        self.calls = 0

    def version(self) -> str:
        return "fake-1"

    def download(
        self,
        source_id: str,
        private_source_uri: str,
        destination: Path,
    ) -> DownloadedAudio:
        self.calls += 1
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = destination / f"{source_id}.{self._extension}"
        path.write_bytes(self._payload)
        for name in self._extra_files:
            (destination / name).write_bytes(b"unexpected")
        return DownloadedAudio(
            path=path,
            format_id="140",
            extension=self._extension,
            audio_codec=self._audio_codec,
            sample_rate_hz=44100,
            bitrate_kbps=129.5,
        )


class _FailingDownloader(_FakeDownloader):
    def download(
        self,
        source_id: str,
        private_source_uri: str,
        destination: Path,
    ) -> DownloadedAudio:
        raise ReferenceAudioIngestError(f"network refused {private_source_uri}")


def test_ingest_stores_audio_and_records_provenance(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    updated, record = ingest_source_audio(
        storage,
        "candidate-001",
        "source-003",
        _FakeDownloader(),
        generated_at="2026-08-09T00:00:00+00:00",
    )

    stored = updated.root / record.relative_path
    assert stored.is_file()
    assert stored.stat().st_mode & 0o777 == 0o600
    assert record.relative_path == "raw/audio/candidate-001/source-003.m4a"
    assert record.byte_size == len(_AUDIO_BYTES)
    assert record.sha256 == sha256_of(stored)
    assert record.audio_codec == "mp4a.40.2"
    assert record.sample_rate_hz == 44100


def test_ingest_registers_the_stored_path_in_the_private_manifest(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    updated, record = ingest_source_audio(
        storage,
        "candidate-001",
        "source-003",
        _FakeDownloader(),
        generated_at="2026-08-09T00:00:00+00:00",
    )

    source = updated.manifest.candidates[0].sources[0]
    assert record.relative_path in source.local_paths
    # The private URI must survive the manifest rewrite untouched.
    assert source.private_source_uri == "https://example.invalid/source-003"


def test_ingest_refuses_to_overwrite_existing_audio(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    updated, _ = ingest_source_audio(
        storage,
        "candidate-001",
        "source-003",
        _FakeDownloader(),
        generated_at="2026-08-09T00:00:00+00:00",
    )

    with pytest.raises(ReferenceAudioIngestError, match="already exists"):
        ingest_source_audio(
            updated,
            "candidate-001",
            "source-003",
            _FakeDownloader(),
            generated_at="2026-08-09T00:00:00+00:00",
        )


def test_ingest_rejects_a_download_that_produced_extra_non_audio_files(
    tmp_path: Path,
) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ReferenceAudioIngestError, match="non-audio files"):
        ingest_source_audio(
            storage,
            "candidate-001",
            "source-003",
            _FakeDownloader(extra_files=("source-003.mp4", "source-003.jpg")),
            generated_at="2026-08-09T00:00:00+00:00",
        )


def test_ingest_rejects_an_unexpected_container(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ReferenceAudioIngestError, match="unexpected container"):
        ingest_source_audio(
            storage,
            "candidate-001",
            "source-003",
            _FakeDownloader(extension="mkv"),
            generated_at="2026-08-09T00:00:00+00:00",
        )


def test_ingest_rejects_a_stream_without_an_audio_codec(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ReferenceAudioIngestError, match="no audio codec"):
        ingest_source_audio(
            storage,
            "candidate-001",
            "source-003",
            _FakeDownloader(audio_codec="none"),
            generated_at="2026-08-09T00:00:00+00:00",
        )


def test_ingest_rejects_a_truncated_download_against_known_duration(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    # A few kilobytes cannot be 1698 seconds of audio.
    with pytest.raises(ReferenceAudioIngestError, match="implausibly small"):
        ingest_source_audio(
            storage,
            "candidate-001",
            "source-003",
            _FakeDownloader(payload=b"tiny"),
            generated_at="2026-08-09T00:00:00+00:00",
            expected_duration_seconds=1698.0,
        )


def test_ingest_rejects_an_empty_file(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ReferenceAudioIngestError, match="empty"):
        ingest_source_audio(
            storage,
            "candidate-001",
            "source-003",
            _FakeDownloader(payload=b""),
            generated_at="2026-08-09T00:00:00+00:00",
        )


def test_ingest_rejects_an_unregistered_source(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ReferenceAudioIngestError, match="source ID is not registered"):
        ingest_source_audio(
            storage,
            "candidate-001",
            "source-999",
            _FakeDownloader(),
            generated_at="2026-08-09T00:00:00+00:00",
        )


def test_ingest_rejects_an_unregistered_candidate(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ReferenceAudioIngestError, match="candidate ID is not registered"):
        ingest_source_audio(
            storage,
            "candidate-999",
            "source-003",
            _FakeDownloader(),
            generated_at="2026-08-09T00:00:00+00:00",
        )


def test_download_failure_does_not_leak_the_private_uri(tmp_path: Path) -> None:
    storage = _storage(tmp_path)

    with pytest.raises(ReferenceAudioIngestError) as caught:
        ingest_source_audio(
            storage,
            "candidate-001",
            "source-003",
            _FailingDownloader(),
            generated_at="2026-08-09T00:00:00+00:00",
        )

    assert "<private-source-uri>" in str(caught.value)
    assert "example.invalid" not in str(caught.value)


def test_public_summary_excludes_the_source_uri(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    _, record = ingest_source_audio(
        storage,
        "candidate-001",
        "source-003",
        _FakeDownloader(),
        generated_at="2026-08-09T00:00:00+00:00",
    )

    assert "example.invalid" not in str(public_ingest_summary(record))


def test_record_is_written_once_with_owner_only_permissions(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    updated, record = ingest_source_audio(
        storage,
        "candidate-001",
        "source-003",
        _FakeDownloader(),
        generated_at="2026-08-09T00:00:00+00:00",
    )

    written = dump_ingest_record(updated, "reports/ingest.json", record)

    assert written.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ReferenceAudioIngestError, match="could not create"):
        dump_ingest_record(updated, "reports/ingest.json", record)


def test_record_path_must_stay_directly_under_reports(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    record = AudioIngestRecord(
        schema_version=1,
        generated_at="2026-08-09T00:00:00+00:00",
        candidate_id="candidate-001",
        source_id="source-003",
        tool_version="fake-1",
        relative_path="raw/audio/candidate-001/source-003.m4a",
        byte_size=1,
        sha256="0" * 64,
        format_id="140",
        extension="m4a",
        audio_codec="mp4a.40.2",
        sample_rate_hz=44100,
        bitrate_kbps=129.5,
        expected_duration_seconds=None,
    )

    for bad_path in ("raw/ingest.json", "reports/nested/ingest.json", "reports/x.txt"):
        with pytest.raises(ReferenceAudioIngestError, match="reports/\\*.json"):
            dump_ingest_record(storage, bad_path, record)
