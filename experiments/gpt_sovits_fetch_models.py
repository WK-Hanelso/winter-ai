"""Fetch the GPT-SoVITS checkpoints into the image at build time.

A file rather than a heredoc inside the Dockerfile. The heredoc form silently
did nothing: this builder does not support it, so ``python -`` read an empty
stdin, exited zero, and the build carried on for three more steps before failing
somewhere unrelated. A build step that cannot fail is worse than one that does.

Everything is downloaded now so that synthesis needs no network later. The
tokenizer files matter as much as the weights — without them ``transformers``
decides the local path must be a repository name and asks the network for a
repo called ``GPT_SoVITS/pretrained_models/chinese-roberta-wwm-ext-large``.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from huggingface_hub import hf_hub_download, list_repo_files

REPO = "lj1995/GPT-SoVITS"
# Whole directories rather than named files: the BERT and HuBERT models need
# their tokenizer and preprocessor configuration alongside the weights, and
# listing files by hand is how those got left out the first time.
DIRECTORIES = ("chinese-hubert-base", "chinese-roberta-wwm-ext-large")
CHECKPOINTS = (
    "s1bert25hz-5kh-longer-epoch=12-step=369668.ckpt",
    "s2G2333k.pth",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--weights-dir", required=True)
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    available = set(list_repo_files(REPO))

    wanted = [name for name in available if name.split("/")[0] in DIRECTORIES]
    wanted += [f"{arguments.weights_dir}/{name}" for name in CHECKPOINTS]

    missing = [name for name in wanted if name not in available]
    if missing:
        print(f"저장소에 없는 파일: {missing}", file=sys.stderr)
        return 1

    for name in wanted:
        hf_hub_download(REPO, name, local_dir=str(arguments.destination))
        print(f"  {name}")

    # Prove the files landed where the engine will look, rather than trusting
    # that the download call placed them correctly.
    for name in wanted:
        path = arguments.destination / name
        if not path.is_file():
            print(f"내려받았지만 위치가 다릅니다: {path}", file=sys.stderr)
            return 1

    print(f"{len(wanted)}개 파일 → {arguments.destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
