"""Run Sortformer diarization and apply it to an existing transcript.

Replaces a hand-written verification pipeline that failed. That approach scored
transcript windows against an enrolled voice and could not separate speakers,
because absolute similarity collapses across acoustically different recordings.
A model trained for diarization does not depend on that comparison at all.

The existing whisper transcript is reused: only speaker labels are added, so the
transcription that was already measured stays exactly as it was.

Only counts and durations are printed. Cue text is never written to stdout.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import os
from pathlib import Path

from nemo.collections.asr.models import SortformerEncLabelModel
import torch

from companion.diarization import (
    DiarizationError,
    parse_prediction,
    parse_spans,
    public_diarization_summary,
    split_cues_by_speaker,
)
from companion.reference_subtitle_probe import parse_webvtt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audio", type=Path, required=True, help="16 kHz mono WAV")
    parser.add_argument("--transcript", type=Path, required=True, help="whisper VTT")
    parser.add_argument("--source-id", required=True)
    parser.add_argument(
        "--model-path",
        type=Path,
        default=Path(
            os.environ.get(
                "DIARIZATION_MODEL", "/opt/diarization/diar_sortformer_4spk-v1.nemo"
            )
        ),
    )
    parser.add_argument("--report-path", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if not arguments.audio.is_file():
            raise DiarizationError("audio file was not found")
        model = SortformerEncLabelModel.restore_from(
            restore_path=str(arguments.model_path), map_location="cpu"
        )
        model.eval()
        with torch.no_grad():
            prediction = model.diarize(audio=str(arguments.audio), batch_size=1)

        spans = parse_spans(parse_prediction(prediction))
        cues = parse_webvtt(arguments.transcript.read_text(encoding="utf-8"))
        pieces = split_cues_by_speaker(cues, spans)

        payload: dict[str, object] = {
            "generated_at": datetime.now(UTC).isoformat(),
            "model": arguments.model_path.name,
            "speaker_span_count": len(spans),
            "cue_count": len(cues),
            **public_diarization_summary(pieces, source_id=arguments.source_id),
        }
    except (DiarizationError, OSError, RuntimeError, ValueError) as error:
        print(json.dumps({"status": "error", "error": str(error)}, ensure_ascii=False))
        return 2

    if arguments.report_path:
        descriptor = os.open(
            arguments.report_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2, sort_keys=True)
            file.write("\n")

    payload["status"] = "ok"
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
