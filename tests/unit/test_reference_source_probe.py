from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from companion.reference_source_probe import (
    PrivateSourceMetadata,
    ReferenceSourceProbeError,
    SourceMetadataRunner,
    YtDlpMetadataRunner,
    dump_private_probe_report,
    private_probe_report,
    probe_candidate_sources,
    public_probe_summary,
)
from companion.reference_storage import (
    PrivateCandidateRef,
    PrivateSourceRef,
    ReferencePrivateManifest,
    ReferenceStorage,
    initialize_reference_storage,
)

FIXED_STORAGE_ID = "10000000-0000-4000-8000-000000000001"
PRIVATE_URI_ONE = "fixture-source://one"
PRIVATE_URI_TWO = "fixture-source://two"


class FakeMetadataRunner(SourceMetadataRunner):
    def __init__(
        self,
        results: dict[str, PrivateSourceMetadata | ReferenceSourceProbeError],
    ) -> None:
        self._results = results

    def version(self) -> str:
        return "test-version"

    def probe(self, source_id: str, private_source_uri: str) -> PrivateSourceMetadata:
        result = self._results[source_id]
        if isinstance(result, ReferenceSourceProbeError):
            raise result
        assert result.private_source_uri == private_source_uri
        return result


def _storage(tmp_path: Path) -> ReferenceStorage:
    repository = tmp_path / "repository"
    root = tmp_path / "external"
    repository.mkdir()
    root.mkdir()
    storage = initialize_reference_storage(
        root,
        repository,
        storage_id=FIXED_STORAGE_ID,
    )
    candidate = PrivateCandidateRef(
        candidate_id="candidate-test",
        private_identity_ref="fixture-private-identity",
        sources=(
            PrivateSourceRef("source-one", PRIVATE_URI_ONE),
            PrivateSourceRef("source-two", PRIVATE_URI_TWO),
        ),
    )
    manifest = ReferencePrivateManifest(
        schema_version=storage.manifest.schema_version,
        storage_id=storage.storage_id,
        candidates=(candidate,),
    )
    return replace(storage, manifest=manifest)


def _metadata(
    source_id: str,
    private_uri: str,
    duration_seconds: float,
) -> PrivateSourceMetadata:
    raw = {
        "title": "Synthetic title",
        "uploader": "Synthetic uploader",
        "duration": duration_seconds,
        "availability": "public",
        "live_status": "not_live",
        "formats": [
            {
                "format_id": "audio-1",
                "ext": "webm",
                "acodec": "opus",
                "asr": 48000,
                "abr": 128.0,
                "protocol": "https",
            },
            {
                "format_id": "video-only",
                "ext": "mp4",
                "acodec": "none",
            },
        ],
        "subtitles": {"ko": []},
        "automatic_captions": {"en": [], "ko": []},
    }
    runner = YtDlpMetadataRunner(
        run_command=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(raw),
            stderr="",
        )
    )
    return runner.probe(source_id, private_uri)


def test_probe_collects_successes_and_sanitizes_public_summary(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    first = _metadata("source-one", PRIVATE_URI_ONE, 120.5)
    second = _metadata("source-two", PRIVATE_URI_TWO, 240.0)

    report = probe_candidate_sources(
        storage,
        "candidate-test",
        FakeMetadataRunner({"source-one": first, "source-two": second}),
        generated_at="2026-01-01T00:00:00+00:00",
        duration_cap_seconds=500,
    )
    public = public_probe_summary(report)
    serialized_public = json.dumps(public)

    assert report.total_duration_seconds == 360.5
    assert report.cap_exceeded is False
    assert public["success_count"] == 2
    assert public["sources"][0]["audio_format_count"] == 1
    assert "private_source_uri" not in serialized_public
    assert PRIVATE_URI_ONE not in serialized_public
    assert "Synthetic title" not in serialized_public

    private = private_probe_report(report)
    assert private["sources"][0]["private_source_uri"] == PRIVATE_URI_ONE
    assert private["sources"][0]["title"] == "Synthetic title"


def test_probe_marks_duration_cap_exceeded(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    first = _metadata("source-one", PRIVATE_URI_ONE, 300.0)
    second = _metadata("source-two", PRIVATE_URI_TWO, 250.0)

    report = probe_candidate_sources(
        storage,
        "candidate-test",
        FakeMetadataRunner({"source-one": first, "source-two": second}),
        generated_at="2026-01-01T00:00:00+00:00",
        duration_cap_seconds=500,
    )

    assert report.cap_exceeded is True
    assert public_probe_summary(report)["cap_exceeded"] is True


def test_probe_records_partial_failure_without_private_uri(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    first = _metadata("source-one", PRIVATE_URI_ONE, 100.0)
    failure = ReferenceSourceProbeError(
        f"could not inspect {PRIVATE_URI_TWO} because access was denied"
    )

    report = probe_candidate_sources(
        storage,
        "candidate-test",
        FakeMetadataRunner({"source-one": first, "source-two": failure}),
        generated_at="2026-01-01T00:00:00+00:00",
    )
    serialized_public = json.dumps(public_probe_summary(report))

    assert len(report.successes) == 1
    assert len(report.failures) == 1
    assert PRIVATE_URI_TWO not in serialized_public
    assert "<private-source-uri>" in serialized_public


def test_probe_rejects_unknown_candidate(tmp_path: Path) -> None:
    with pytest.raises(ReferenceSourceProbeError, match="candidate ID"):
        probe_candidate_sources(
            _storage(tmp_path),
            "missing-candidate",
            FakeMetadataRunner({}),
            generated_at="2026-01-01T00:00:00+00:00",
        )


def test_runner_rejects_failed_process_and_redacts_uri() -> None:
    private_uri = "fixture-source://must-not-leak"
    runner = YtDlpMetadataRunner(
        run_command=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=1,
            stdout="",
            stderr=f"failed to load {private_uri}",
        )
    )

    with pytest.raises(ReferenceSourceProbeError) as captured:
        runner.probe("source-one", private_uri)

    assert private_uri not in str(captured.value)
    assert "<private-source-uri>" in str(captured.value)


def test_runner_rejects_malformed_json() -> None:
    runner = YtDlpMetadataRunner(
        run_command=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="not-json", stderr=""
        )
    )

    with pytest.raises(ReferenceSourceProbeError, match="malformed metadata JSON"):
        runner.probe("source-one", PRIVATE_URI_ONE)


def test_runner_rejects_metadata_without_audio() -> None:
    runner = YtDlpMetadataRunner(
        run_command=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[],
            returncode=0,
            stdout=json.dumps(
                {
                    "duration": 100,
                    "formats": [{"format_id": "video", "acodec": "none"}],
                }
            ),
            stderr="",
        )
    )

    with pytest.raises(ReferenceSourceProbeError, match="no usable audio formats"):
        runner.probe("source-one", PRIVATE_URI_ONE)


def test_private_report_is_mode_0600_and_never_overwritten(tmp_path: Path) -> None:
    storage = _storage(tmp_path)
    first = _metadata("source-one", PRIVATE_URI_ONE, 100.0)
    second = _metadata("source-two", PRIVATE_URI_TWO, 200.0)
    report = probe_candidate_sources(
        storage,
        "candidate-test",
        FakeMetadataRunner({"source-one": first, "source-two": second}),
        generated_at="2026-01-01T00:00:00+00:00",
    )

    destination = dump_private_probe_report(
        storage,
        "reports/source-metadata.json",
        report,
    )

    assert destination.stat().st_mode & 0o777 == 0o600
    assert PRIVATE_URI_ONE in destination.read_text(encoding="utf-8")
    with pytest.raises(ReferenceSourceProbeError, match="could not create"):
        dump_private_probe_report(storage, "reports/source-metadata.json", report)


@pytest.mark.parametrize(
    "relative_path",
    (
        "raw/source-metadata.json",
        "reports/nested/source-metadata.json",
        "reports/source-metadata.txt",
        "../reports/source-metadata.json",
    ),
)
def test_private_report_rejects_unmanaged_report_path(
    tmp_path: Path,
    relative_path: str,
) -> None:
    storage = _storage(tmp_path)
    first = _metadata("source-one", PRIVATE_URI_ONE, 100.0)
    second = _metadata("source-two", PRIVATE_URI_TWO, 200.0)
    report = probe_candidate_sources(
        storage,
        "candidate-test",
        FakeMetadataRunner({"source-one": first, "source-two": second}),
        generated_at="2026-01-01T00:00:00+00:00",
    )

    with pytest.raises(ReferenceSourceProbeError, match=r"reports/\*.json"):
        dump_private_probe_report(storage, relative_path, report)


def test_runner_version_is_required() -> None:
    runner = YtDlpMetadataRunner(
        run_command=lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout="\n", stderr=""
        )
    )

    with pytest.raises(ReferenceSourceProbeError, match="empty version"):
        runner.version()
