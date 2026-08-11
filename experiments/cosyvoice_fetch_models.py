"""Fetch the CosyVoice 3 checkpoint into the image at build time.

Written as a file rather than a Dockerfile heredoc. The heredoc form silently
did nothing in the GPT-SoVITS image — this builder does not support it, so
``python -`` read empty stdin, exited zero, and five builds ran without ever
downloading a model. A build step that cannot fail is worse than one that does.

Everything is fetched now so synthesis needs no network later, and the files are
checked afterwards rather than assumed: calling a download function and having
the bytes on disk are different claims.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from huggingface_hub import list_repo_files, snapshot_download

REPO = "FunAudioLLM/Fun-CosyVoice3-0.5B-2512"
# Named so a missing one fails here rather than at the first synthesis. The
# tokenizer and speaker-embedding ONNX files matter as much as the weights.
REQUIRED = (
    "cosyvoice3.yaml",
    "llm.pt",
    "flow.pt",
    "hift.pt",
    "campplus.onnx",
    "speech_tokenizer_v3.onnx",
    "CosyVoice-BlankEN/config.json",
    "CosyVoice-BlankEN/model.safetensors",
    "CosyVoice-BlankEN/vocab.json",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    available = set(list_repo_files(REPO))
    missing = [name for name in REQUIRED if name not in available]
    if missing:
        print(f"저장소에 없는 파일: {missing}", file=sys.stderr)
        return 1

    # The whole snapshot, minus the demo assets: the loader reads several files
    # this list does not name, and guessing which ones is how the GPT-SoVITS
    # image ended up missing its tokenizer configuration.
    snapshot_download(
        REPO,
        local_dir=str(arguments.destination),
        ignore_patterns=["asset/*", "*.png", ".gitattributes"],
    )

    absent = [name for name in REQUIRED if not (arguments.destination / name).is_file()]
    if absent:
        print(f"내려받았지만 위치가 다릅니다: {absent}", file=sys.stderr)
        return 1

    total = sum(path.stat().st_size for path in arguments.destination.rglob("*") if path.is_file())
    print(f"{arguments.destination} | {total / 1024 ** 3:.2f} GiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
