"""No-download metadata probe for private Human Reference sources."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import json
import math
import os
from pathlib import Path, PurePosixPath
import subprocess
from tempfile import TemporaryDirectory
from typing import Any, Protocol

from companion.reference_storage import ReferenceStorage

PROBE_SCHEMA_VERSION = 1
DEFAULT_DURATION_CAP_SECONDS = 3 * 60 * 60


class ReferenceSourceProbeError(RuntimeError):
    """Raised when source metadata cannot be probed without ambiguity."""


@dataclass(frozen=True)
class AudioFormatMetadata:
    format_id: str
    extension: str | None
    audio_codec: str
    sample_rate_hz: int | None
    bitrate_kbps: float | None
    protocol: str | None


@dataclass(frozen=True)
class PrivateSourceMetadata:
    source_id: str
    private_source_uri: str
    title: str | None
    uploader: str | None
    duration_seconds: float
    availability: str
    live_status: str | None
    audio_formats: tuple[AudioFormatMetadata, ...]
    subtitle_languages: tuple[str, ...]
    automatic_caption_languages: tuple[str, ...]


@dataclass(frozen=True)
class SourceProbeFailure:
    source_id: str
    error: str


@dataclass(frozen=True)
class SourceProbeReport:
    schema_version: int
    generated_at: str
    candidate_id: str
    tool_version: str
    duration_cap_seconds: int
    successes: tuple[PrivateSourceMetadata, ...]
    failures: tuple[SourceProbeFailure, ...]

    @property
    def total_duration_seconds(self) -> float:
        return sum(source.duration_seconds for source in self.successes)

    @property
    def cap_exceeded(self) -> bool:
        return self.total_duration_seconds > self.duration_cap_seconds


class SourceMetadataRunner(Protocol):
    def version(self) -> str:
        ...

    def probe(self, source_id: str, private_source_uri: str) -> PrivateSourceMetadata:
        ...


class YtDlpMetadataRunner:
    """Run a pinned yt-dlp executable in simulation mode only."""

    def __init__(
        self,
        executable: str = "yt-dlp",
        *,
        run_command: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    ) -> None:
        self._executable = executable
        self._run_command = run_command

    def version(self) -> str:
        completed = self._run(
            [self._executable, "--version"],
            private_source_uri=None,
        )
        version = completed.stdout.strip()
        if not version:
            raise ReferenceSourceProbeError("yt-dlp returned an empty version")
        return version

    def probe(self, source_id: str, private_source_uri: str) -> PrivateSourceMetadata:
        with TemporaryDirectory(prefix="winter-source-probe-") as temporary:
            completed = self._run(
                [
                    self._executable,
                    "--ignore-config",
                    "--no-cache-dir",
                    "--no-playlist",
                    "--simulate",
                    "--dump-single-json",
                    "--no-warnings",
                    private_source_uri,
                ],
                private_source_uri=private_source_uri,
                cwd=temporary,
            )
            if any(Path(temporary).iterdir()):
                raise ReferenceSourceProbeError(
                    "metadata probe unexpectedly wrote files in its temporary directory"
                )
        try:
            raw = json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise ReferenceSourceProbeError("yt-dlp returned malformed metadata JSON") from error
        if not isinstance(raw, dict):
            raise ReferenceSourceProbeError("yt-dlp metadata must be a JSON object")
        return _metadata_from_yt_dlp(source_id, private_source_uri, raw)

    def _run(
        self,
        command: list[str],
        *,
        private_source_uri: str | None,
        cwd: str | None = None,
    ) -> subprocess.CompletedProcess[str]:
        environment = os.environ.copy()
        environment["YTDLP_NO_PLUGINS"] = "1"
        completed = self._run_command(
            command,
            capture_output=True,
            text=True,
            check=False,
            cwd=cwd,
            env=environment,
        )
        if completed.returncode != 0:
            detail = completed.stderr.strip() or "no diagnostic output"
            if private_source_uri:
                detail = detail.replace(private_source_uri, "<private-source-uri>")
            if len(detail) > 500:
                detail = f"{detail[:497]}..."
            raise ReferenceSourceProbeError(f"yt-dlp metadata probe failed: {detail}")
        return completed


def probe_candidate_sources(
    storage: ReferenceStorage,
    candidate_id: str,
    runner: SourceMetadataRunner,
    *,
    generated_at: str,
    duration_cap_seconds: int = DEFAULT_DURATION_CAP_SECONDS,
) -> SourceProbeReport:
    """Probe only the sources explicitly registered to one private candidate."""
    if duration_cap_seconds <= 0:
        raise ReferenceSourceProbeError("duration cap must be positive")
    candidate = next(
        (
            candidate
            for candidate in storage.manifest.candidates
            if candidate.candidate_id == candidate_id
        ),
        None,
    )
    if candidate is None:
        raise ReferenceSourceProbeError("candidate ID is not registered in private manifest")
    if not candidate.sources:
        raise ReferenceSourceProbeError("candidate has no approved sources")

    successes: list[PrivateSourceMetadata] = []
    failures: list[SourceProbeFailure] = []
    for source in candidate.sources:
        try:
            metadata = runner.probe(source.source_id, source.private_source_uri)
        except ReferenceSourceProbeError as error:
            message = str(error).replace(source.private_source_uri, "<private-source-uri>")
            failures.append(SourceProbeFailure(source_id=source.source_id, error=message))
        else:
            if metadata.source_id != source.source_id:
                raise ReferenceSourceProbeError("metadata runner returned a different source ID")
            if metadata.private_source_uri != source.private_source_uri:
                raise ReferenceSourceProbeError(
                    "metadata runner returned a different private source URI"
                )
            successes.append(metadata)

    return SourceProbeReport(
        schema_version=PROBE_SCHEMA_VERSION,
        generated_at=generated_at,
        candidate_id=candidate_id,
        tool_version=runner.version(),
        duration_cap_seconds=duration_cap_seconds,
        successes=tuple(successes),
        failures=tuple(failures),
    )


def public_probe_summary(report: SourceProbeReport) -> dict[str, Any]:
    """Return an intentionally sanitized summary suitable for terminal output."""
    return {
        "schema_version": report.schema_version,
        "candidate_id": report.candidate_id,
        "tool_version": report.tool_version,
        "duration_cap_seconds": report.duration_cap_seconds,
        "total_duration_seconds": report.total_duration_seconds,
        "cap_exceeded": report.cap_exceeded,
        "source_count": len(report.successes) + len(report.failures),
        "success_count": len(report.successes),
        "failure_count": len(report.failures),
        "sources": [
            {
                "source_id": source.source_id,
                "duration_seconds": source.duration_seconds,
                "availability": source.availability,
                "live_status": source.live_status,
                "audio_format_count": len(source.audio_formats),
                "subtitle_languages": list(source.subtitle_languages),
                "automatic_caption_languages": list(
                    source.automatic_caption_languages
                ),
            }
            for source in report.successes
        ],
        "failures": [
            {"source_id": failure.source_id, "error": failure.error}
            for failure in report.failures
        ],
    }


def private_probe_report(report: SourceProbeReport) -> dict[str, Any]:
    """Return the detailed report that must stay in managed external storage."""
    payload = public_probe_summary(report)
    payload["generated_at"] = report.generated_at
    payload["sources"] = [
        {
            "source_id": source.source_id,
            "private_source_uri": source.private_source_uri,
            "title": source.title,
            "uploader": source.uploader,
            "duration_seconds": source.duration_seconds,
            "availability": source.availability,
            "live_status": source.live_status,
            "audio_formats": [
                {
                    "format_id": audio_format.format_id,
                    "extension": audio_format.extension,
                    "audio_codec": audio_format.audio_codec,
                    "sample_rate_hz": audio_format.sample_rate_hz,
                    "bitrate_kbps": audio_format.bitrate_kbps,
                    "protocol": audio_format.protocol,
                }
                for audio_format in source.audio_formats
            ],
            "subtitle_languages": list(source.subtitle_languages),
            "automatic_caption_languages": list(
                source.automatic_caption_languages
            ),
        }
        for source in report.successes
    ]
    return payload


def dump_private_probe_report(
    storage: ReferenceStorage,
    relative_path: str,
    report: SourceProbeReport,
) -> Path:
    """Create a mode-0600 report under reports/ without overwriting."""
    normalized = PurePosixPath(relative_path)
    if (
        len(normalized.parts) != 2
        or normalized.parts[0] != "reports"
        or normalized.suffix != ".json"
    ):
        raise ReferenceSourceProbeError(
            "private source probe report must be a direct reports/*.json path"
        )
    destination = storage.path(relative_path)
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ReferenceSourceProbeError("private report directory is missing or unsafe")
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(
                private_probe_report(report),
                file,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            file.write("\n")
    except OSError as error:
        raise ReferenceSourceProbeError(
            "could not create private source probe report"
        ) from error
    return destination


def _metadata_from_yt_dlp(
    source_id: str,
    private_source_uri: str,
    raw: dict[str, Any],
) -> PrivateSourceMetadata:
    duration = _positive_number(raw.get("duration"), "duration")
    formats = raw.get("formats")
    if not isinstance(formats, list):
        raise ReferenceSourceProbeError("yt-dlp formats must be a list")
    audio_formats: list[AudioFormatMetadata] = []
    seen_format_ids: set[str] = set()
    for item in formats:
        if not isinstance(item, dict):
            raise ReferenceSourceProbeError("yt-dlp format entry must be an object")
        audio_codec = item.get("acodec")
        if not isinstance(audio_codec, str) or audio_codec == "none":
            continue
        format_id = item.get("format_id")
        if not isinstance(format_id, str) or not format_id:
            raise ReferenceSourceProbeError("audio format has an invalid format ID")
        if format_id in seen_format_ids:
            continue
        seen_format_ids.add(format_id)
        audio_formats.append(
            AudioFormatMetadata(
                format_id=format_id,
                extension=_optional_string(item.get("ext")),
                audio_codec=audio_codec,
                sample_rate_hz=_optional_positive_integer(item.get("asr"), "audio asr"),
                bitrate_kbps=_optional_positive_number(item.get("abr"), "audio abr"),
                protocol=_optional_string(item.get("protocol")),
            )
        )
    if not audio_formats:
        raise ReferenceSourceProbeError("source exposes no usable audio formats")

    return PrivateSourceMetadata(
        source_id=source_id,
        private_source_uri=private_source_uri,
        title=_optional_string(raw.get("title")),
        uploader=_optional_string(raw.get("uploader")),
        duration_seconds=duration,
        availability=_optional_string(raw.get("availability")) or "unknown",
        live_status=_optional_string(raw.get("live_status")),
        audio_formats=tuple(audio_formats),
        subtitle_languages=_language_keys(raw.get("subtitles"), "subtitles"),
        automatic_caption_languages=_language_keys(
            raw.get("automatic_captions"), "automatic captions"
        ),
    )


def _language_keys(value: Any, label: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ReferenceSourceProbeError(f"yt-dlp {label} must be an object")
    if not all(isinstance(language, str) and language for language in value):
        raise ReferenceSourceProbeError(f"yt-dlp {label} has an invalid language")
    return tuple(sorted(value))


def _positive_number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ReferenceSourceProbeError(f"yt-dlp {label} must be a positive number")
    converted = float(value)
    if not math.isfinite(converted) or converted <= 0:
        raise ReferenceSourceProbeError(f"yt-dlp {label} must be a positive number")
    return converted


def _optional_positive_number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    return _positive_number(value, label)


def _optional_positive_integer(value: Any, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ReferenceSourceProbeError(f"yt-dlp {label} must be a positive integer")
    return value


def _optional_string(value: Any) -> str | None:
    return value if isinstance(value, str) and value else None
