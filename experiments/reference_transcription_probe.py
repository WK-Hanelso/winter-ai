"""Host-side STT pilot: cut a segment, transcribe it, compare against the service caption.

Follows the same pattern as ``stt_probe.py``: this script runs on the Host and
drives pinned Docker images. Both ffmpeg and whisper-cli live in the whisper.cpp
image, so no additional runtime is installed anywhere.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import UTC, datetime
import json
import os
from pathlib import Path
import shlex
import subprocess
import time

from companion.reference_transcription_probe import (
    ReferenceTranscriptionProbeError,
    build_segment_command,
    build_whisper_command,
    compare_transcripts,
    public_transcription_summary,
    validate_segment,
)

DEFAULT_IMAGE = "ghcr.io/ggml-org/whisper.cpp:main-vulkan"


def build_docker_command(
    *,
    image: str,
    model_dir: Path,
    work_dir: Path,
    inner_command: list[str],
    user: str | None = None,
) -> list[str]:
    """Build one pinned-image run.

    ``--user`` matters: without it the container writes its output as root and
    the Host cannot even set permissions on the transcript it just produced.
    """
    command = ["docker", "run", "--rm"]
    if user:
        command.extend(["--user", user])
    command.extend(
        [
            "--entrypoint",
            inner_command[0],
            "-v",
            f"{model_dir}:/models:ro",
            "-v",
            f"{work_dir}:/work",
            image,
            *inner_command[1:],
        ]
    )
    return command


def host_user() -> str:
    return f"{os.getuid()}:{os.getgid()}"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio-path", type=Path, required=True)
    parser.add_argument("--reference-vtt", type=Path, required=True)
    parser.add_argument("--model-path", type=Path, required=True)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--source-id", required=True)
    parser.add_argument("--start-seconds", type=float, default=0.0)
    parser.add_argument("--duration-seconds", type=float, required=True)
    parser.add_argument("--language", default="ko")
    parser.add_argument("--threads", type=int)
    parser.add_argument("--image", default=DEFAULT_IMAGE)
    return parser.parse_args(argv)


def _run(command: list[str], label: str) -> None:
    print(f"Running ({label}):", shlex.join(command), flush=True)
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise ReferenceTranscriptionProbeError(
            f"{label} failed with exit code {completed.returncode}"
        )


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        segment = validate_segment(args.start_seconds, args.duration_seconds)
        audio_path = args.audio_path.resolve()
        model_path = args.model_path.resolve()
        work_dir = args.work_dir.resolve()
        if not audio_path.is_file():
            raise ReferenceTranscriptionProbeError("audio file was not found")
        if not model_path.is_file():
            raise ReferenceTranscriptionProbeError("whisper model was not found")
        work_dir.mkdir(mode=0o700, parents=True, exist_ok=True)

        stem = f"{args.source_id}-{int(segment.start_seconds)}-{int(segment.duration_seconds)}"
        wav_name = f"{stem}.wav"
        # The source audio is staged inside the work directory so one bind mount
        # covers the input and every produced file. Keyed by source, not by
        # segment: staging per segment would copy the whole file again each run.
        staged_input = work_dir / f"{args.source_id}{audio_path.suffix}"
        if not staged_input.exists():
            staged_input.write_bytes(audio_path.read_bytes())
            staged_input.chmod(0o600)

        _run(
            build_docker_command(
                image=args.image,
                model_dir=model_path.parent,
                work_dir=work_dir,
                user=host_user(),
                inner_command=build_segment_command(
                    input_path=Path("/work") / staged_input.name,
                    output_path=Path("/work") / wav_name,
                    segment=segment,
                ),
            ),
            "ffmpeg segment",
        )

        started = time.monotonic()
        _run(
            build_docker_command(
                image=args.image,
                model_dir=model_path.parent,
                work_dir=work_dir,
                user=host_user(),
                inner_command=build_whisper_command(
                    model_path=Path("/models") / model_path.name,
                    audio_path=Path("/work") / wav_name,
                    output_prefix=Path("/work") / stem,
                    language=args.language,
                    threads=args.threads,
                ),
            ),
            "whisper transcription",
        )
        elapsed = time.monotonic() - started

        # Every artefact derived from private audio stays owner-only, including
        # the intermediate WAV that the container just wrote with a default mask.
        (work_dir / wav_name).chmod(0o600)
        local_vtt = work_dir / f"{stem}.vtt"
        if not local_vtt.is_file():
            raise ReferenceTranscriptionProbeError("whisper produced no VTT output")
        local_vtt.chmod(0o600)

        comparison = compare_transcripts(
            args.source_id,
            segment,
            local_vtt.read_text(encoding="utf-8"),
            args.reference_vtt.read_text(encoding="utf-8"),
            generated_at=datetime.now(UTC).isoformat(),
            model_name=model_path.name,
            elapsed_seconds=elapsed,
        )
    except (ReferenceTranscriptionProbeError, OSError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    summary = public_transcription_summary(comparison)
    summary["status"] = "ok"
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
