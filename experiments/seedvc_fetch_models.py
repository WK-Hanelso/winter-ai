"""Fetch seed-vc's checkpoints into the image at build time.

The repository downloads them on first use into ``./checkpoints``. Doing it here
instead keeps a run offline, and makes a missing file a build failure rather
than a surprise mid-conversion.

The names come from ``inference.py`` rather than from documentation, because
that is what will actually be asked for. Only the non-f0 path is fetched: f0
conditioning is for singing conversion, and this is for speech.

Two of them are named in the *config* rather than the code — the content encoder
and the vocoder — and were missed the first time. The run then failed on a
download the offline setting had blocked, which is the failure working: without
it the container would have quietly reached for the network on every start.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

from huggingface_hub import hf_hub_download, snapshot_download

# (repo, filename) exactly as inference.py asks for them.
REQUIRED: tuple[tuple[str, str], ...] = (
    ("Plachta/Seed-VC", "DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth"),
    ("Plachta/Seed-VC", "config_dit_mel_seed_uvit_whisper_small_wavenet.yml"),
    ("funasr/campplus", "campplus_cn_common.bin"),
    ("FunAudioLLM/CosyVoice-300M", "hift.pt"),
)
# Whole repositories, because they are loaded by ``from_pretrained`` and it
# reads several files this list would have to guess at. Named in the model
# config rather than in the code: speech_tokenizer.name and vocoder.name.
SNAPSHOTS: tuple[str, ...] = (
    "openai/whisper-small",
    "nvidia/bigvgan_v2_22khz_80band_256x",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    arguments.destination.mkdir(parents=True, exist_ok=True)
    for repo, filename in REQUIRED:
        # cache_dir, not local_dir: the repository calls hf_hub_download with a
        # cache_dir, so the layout has to be the one that lookup expects.
        path = hf_hub_download(repo_id=repo, filename=filename,
                               cache_dir=str(arguments.destination))
        if not Path(path).is_file():
            print(f"내려받았지만 파일이 없습니다: {path}", file=sys.stderr)
            return 1
        print(f"  {repo}/{filename}")

    for repo in SNAPSHOTS:
        # The default cache, not the checkpoints directory: from_pretrained
        # looks there rather than where seed-vc puts its own files.
        snapshot_download(repo_id=repo, ignore_patterns=["*.msgpack", "*.h5", "*.ot"])
        print(f"  {repo} (전체)")

    total = sum(p.stat().st_size for p in arguments.destination.rglob("*") if p.is_file())
    print(f"{arguments.destination} | {total / 1024 ** 2:.0f} MiB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
