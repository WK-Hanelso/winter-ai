"""Fetch the audio stream of one approved source into private storage.

This is the first module that stores real media. Two properties matter more
than convenience:

* one source per run, named explicitly, so a mistake costs one file;
* no re-encoding. The probe runtime has no ffmpeg on purpose, so the original
  stream is stored as delivered and nothing silently degrades the voice
  reference.

Only sanitized aggregates are safe to print.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
from typing import Any, Protocol

from companion.reference_storage import (
    PrivateCandidateRef,
    PrivateSourceRef,
    ReferencePrivateManifest,
    ReferenceStorage,
    save_private_manifest,
)

AUDIO_INGEST_SCHEMA_VERSION = 1

# Containers yt-dlp can deliver for an audio-only stream without ffmpeg.
ALLOWED_AUDIO_SUFFIXES = frozenset({".m4a", ".webm", ".opus", ".mp4a", ".mp3", ".ogg"})

# A 28 minute track cannot plausibly be smaller than this. Guards against a
# truncated or error-page download being accepted as success.
MINIMUM_BYTES_PER_SECOND = 1000


class ReferenceAudioIngestError(RuntimeError):
    """Raised when audio cannot be stored with verifiable provenance."""


@dataclass(frozen=True)
class DownloadedAudio:
    """What the adapter reports back about one downloaded stream."""

    path: Path
    format_id: str
    extension: str
    audio_codec: str
    sample_rate_hz: int | None
    bitrate_kbps: float | None


@dataclass(frozen=True)
class AudioIngestRecord:
    schema_version: int
    generated_at: str
    candidate_id: str
    source_id: str
    tool_version: str
    relative_path: str
    byte_size: int
    sha256: str
    format_id: str
    extension: str
    audio_codec: str
    sample_rate_hz: int | None
    bitrate_kbps: float | None
    expected_duration_seconds: float | None


class AudioDownloader(Protocol):
    def version(self) -> str:
        ...

    def download(
        self,
        source_id: str,
        private_source_uri: str,
        destination: Path,
    ) -> DownloadedAudio:
        ...


class YtDlpAudioDownloader:
    """Download the best audio-only stream with a pinned yt-dlp executable."""

    _PRINT_FIELDS = (
        "%(format_id)s\t%(ext)s\t%(acodec)s\t%(asr)s\t%(abr)s\t%(filepath)s"
    )

    def __init__(
        self,
        executable: str = "yt-dlp",
        *,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._executable = executable
        self._run_command = run_command

    def version(self) -> str:
        completed = self._run([self._executable, "--version"], private_source_uri=None)
        version = completed.stdout.strip()
        if not version:
            raise ReferenceAudioIngestError("yt-dlp returned an empty version")
        return version

    def download(
        self,
        source_id: str,
        private_source_uri: str,
        destination: Path,
    ) -> DownloadedAudio:
        destination.mkdir(mode=0o700, parents=True, exist_ok=True)
        completed = self._run(
            [
                self._executable,
                "--ignore-config",
                "--no-cache-dir",
                "--no-playlist",
                "--no-warnings",
                # Audio only, delivered as-is. No ffmpeg means no re-encode.
                "--format",
                "bestaudio",
                "--no-write-subs",
                "--no-write-auto-subs",
                "--no-write-thumbnail",
                "--no-write-info-json",
                "--output",
                str(destination / f"{source_id}.%(ext)s"),
                "--no-simulate",
                "--print",
                f"after_move:{self._PRINT_FIELDS}",
                private_source_uri,
            ],
            private_source_uri=private_source_uri,
        )
        return _parse_download_report(completed.stdout, destination)

    def _run(
        self,
        command: list[str],
        *,
        private_source_uri: str | None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["YTDLP_NO_PLUGINS"] = "1"
        completed = self._run_command(
            command,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "no diagnostic output"
            if private_source_uri:
                detail = detail.replace(private_source_uri, "<private-source-uri>")
            if len(detail) > 500:
                detail = f"{detail[:497]}..."
            raise ReferenceAudioIngestError(f"yt-dlp audio download failed: {detail}")
        return completed


def ingest_source_audio(
    storage: ReferenceStorage,
    candidate_id: str,
    source_id: str,
    downloader: AudioDownloader,
    *,
    generated_at: str,
    audio_relative_directory: str = "raw/audio",
    expected_duration_seconds: float | None = None,
) -> tuple[ReferenceStorage, AudioIngestRecord]:
    """Download one named source and register it in the private manifest."""
    candidate, source = _locate(storage.manifest, candidate_id, source_id)

    base = storage.path(audio_relative_directory)
    if base.is_symlink() or not base.is_dir():
        raise ReferenceAudioIngestError("audio directory is missing or unsafe")
    destination = base / candidate_id
    if destination.exists() and any(
        entry.stem == source_id for entry in destination.iterdir()
    ):
        raise ReferenceAudioIngestError(
            f"audio for {source_id} already exists; refusing to overwrite"
        )

    try:
        downloaded = downloader.download(source_id, source.private_source_uri, destination)
    except ReferenceAudioIngestError as error:
        raise ReferenceAudioIngestError(
            str(error).replace(source.private_source_uri, "<private-source-uri>")
        ) from None

    # Verify the chosen file first so a bad container is reported as such, then
    # sweep the directory for anything else the download left behind.
    record = _verify_and_describe(
        storage,
        candidate_id,
        source_id,
        downloaded,
        downloader.version(),
        generated_at,
        expected_duration_seconds,
    )
    _reject_unexpected_files(destination, source_id)
    updated = _register_local_path(storage, candidate, source, record.relative_path)
    return updated, record


def public_ingest_summary(record: AudioIngestRecord) -> dict[str, Any]:
    """Return a summary safe for terminal output; it names no source URI."""
    return {
        "schema_version": record.schema_version,
        "candidate_id": record.candidate_id,
        "source_id": record.source_id,
        "tool_version": record.tool_version,
        "relative_path": record.relative_path,
        "byte_size": record.byte_size,
        "megabytes": round(record.byte_size / 1_048_576, 2),
        "sha256": record.sha256,
        "format_id": record.format_id,
        "extension": record.extension,
        "audio_codec": record.audio_codec,
        "sample_rate_hz": record.sample_rate_hz,
        "bitrate_kbps": record.bitrate_kbps,
        "expected_duration_seconds": record.expected_duration_seconds,
    }


def dump_ingest_record(
    storage: ReferenceStorage,
    relative_path: str,
    record: AudioIngestRecord,
) -> Path:
    """Create a mode-0600 report under reports/ without overwriting."""
    normalized = PurePosixPath(relative_path)
    if (
        len(normalized.parts) != 2
        or normalized.parts[0] != "reports"
        or normalized.suffix != ".json"
    ):
        raise ReferenceAudioIngestError(
            "audio ingest record must be a direct reports/*.json path"
        )
    destination = storage.path(relative_path)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ReferenceAudioIngestError("private report directory is missing or unsafe")
    payload = public_ingest_summary(record)
    payload["generated_at"] = record.generated_at
    try:
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")
    except OSError as error:
        raise ReferenceAudioIngestError("could not create audio ingest record") from error
    return destination


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as file:
            for block in iter(lambda: file.read(1_048_576), b""):
                digest.update(block)
    except OSError as error:
        raise ReferenceAudioIngestError("could not read stored audio") from error
    return digest.hexdigest()


def _locate(
    manifest: ReferencePrivateManifest,
    candidate_id: str,
    source_id: str,
) -> tuple[PrivateCandidateRef, PrivateSourceRef]:
    candidate = next(
        (entry for entry in manifest.candidates if entry.candidate_id == candidate_id),
        None,
    )
    if candidate is None:
        raise ReferenceAudioIngestError("candidate ID is not registered in private manifest")
    source = next(
        (entry for entry in candidate.sources if entry.source_id == source_id),
        None,
    )
    if source is None:
        raise ReferenceAudioIngestError("source ID is not registered for this candidate")
    return candidate, source


def _verify_and_describe(
    storage: ReferenceStorage,
    candidate_id: str,
    source_id: str,
    downloaded: DownloadedAudio,
    tool_version: str,
    generated_at: str,
    expected_duration_seconds: float | None,
) -> AudioIngestRecord:
    path = downloaded.path
    if path.is_symlink() or not path.is_file():
        raise ReferenceAudioIngestError("stored audio is missing or unsafe")
    if path.suffix.lower() not in ALLOWED_AUDIO_SUFFIXES:
        raise ReferenceAudioIngestError(
            f"stored audio has an unexpected container: {path.suffix!r}"
        )
    if downloaded.audio_codec in {"", "none"}:
        raise ReferenceAudioIngestError("downloaded stream reports no audio codec")

    byte_size = path.stat().st_size
    if byte_size <= 0:
        raise ReferenceAudioIngestError("stored audio is empty")
    if expected_duration_seconds is not None:
        minimum = expected_duration_seconds * MINIMUM_BYTES_PER_SECOND
        if byte_size < minimum:
            raise ReferenceAudioIngestError(
                "stored audio is implausibly small for the expected duration"
            )
    path.chmod(0o600)

    try:
        relative = path.resolve().relative_to(storage.root).as_posix()
    except ValueError as error:
        raise ReferenceAudioIngestError("stored audio escaped the storage root") from error

    return AudioIngestRecord(
        schema_version=AUDIO_INGEST_SCHEMA_VERSION,
        generated_at=generated_at,
        candidate_id=candidate_id,
        source_id=source_id,
        tool_version=tool_version,
        relative_path=relative,
        byte_size=byte_size,
        sha256=sha256_of(path),
        format_id=downloaded.format_id,
        extension=downloaded.extension,
        audio_codec=downloaded.audio_codec,
        sample_rate_hz=downloaded.sample_rate_hz,
        bitrate_kbps=downloaded.bitrate_kbps,
        expected_duration_seconds=expected_duration_seconds,
    )


def _register_local_path(
    storage: ReferenceStorage,
    candidate: PrivateCandidateRef,
    source: PrivateSourceRef,
    relative_path: str,
) -> ReferenceStorage:
    if relative_path in source.local_paths:
        return storage
    updated_source = PrivateSourceRef(
        source_id=source.source_id,
        private_source_uri=source.private_source_uri,
        local_paths=source.local_paths + (relative_path,),
    )
    updated_candidate = PrivateCandidateRef(
        candidate_id=candidate.candidate_id,
        private_identity_ref=candidate.private_identity_ref,
        sources=tuple(
            updated_source if entry.source_id == source.source_id else entry
            for entry in candidate.sources
        ),
    )
    manifest = ReferencePrivateManifest(
        schema_version=storage.manifest.schema_version,
        storage_id=storage.manifest.storage_id,
        candidates=tuple(
            updated_candidate if entry.candidate_id == candidate.candidate_id else entry
            for entry in storage.manifest.candidates
        ),
    )
    return save_private_manifest(storage, manifest)


def _parse_download_report(stdout: str, destination: Path) -> DownloadedAudio:
    lines = [line for line in stdout.splitlines() if line.strip()]
    if not lines:
        raise ReferenceAudioIngestError("yt-dlp reported no downloaded audio")
    if len(lines) > 1:
        raise ReferenceAudioIngestError(
            "yt-dlp reported more than one download; expected a single stream"
        )
    fields = lines[0].split("\t")
    if len(fields) != 6:
        raise ReferenceAudioIngestError("yt-dlp download report has unexpected fields")
    format_id, extension, audio_codec, sample_rate, bitrate, filepath = fields
    if not format_id or not filepath:
        raise ReferenceAudioIngestError("yt-dlp download report is incomplete")
    path = Path(filepath)
    if not path.is_absolute():
        path = destination / path.name
    return DownloadedAudio(
        path=path,
        format_id=format_id,
        extension=extension,
        audio_codec=audio_codec,
        sample_rate_hz=_optional_int(sample_rate),
        bitrate_kbps=_optional_float(bitrate),
    )


def _reject_unexpected_files(destination: Path, source_id: str) -> None:
    """Fail if anything other than this source's audio file appeared."""
    unexpected = [
        entry.name
        for entry in destination.iterdir()
        if entry.is_file()
        and entry.stem == source_id
        and entry.suffix.lower() not in ALLOWED_AUDIO_SUFFIXES
    ]
    if unexpected:
        raise ReferenceAudioIngestError(
            f"audio download unexpectedly produced {len(unexpected)} non-audio files"
        )


def _optional_int(value: str) -> int | None:
    if value in {"", "NA", "None"}:
        return None
    try:
        parsed = int(float(value))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _optional_float(value: str) -> float | None:
    if value in {"", "NA", "None"}:
        return None
    try:
        parsed = float(value)
    except ValueError:
        return None
    return parsed if parsed > 0 else None
