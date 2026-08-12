"""How close does stage 2 get to the Reference's own voice?

Stage 1 makes speech, stage 2 makes it hers. Judging that by ear is the final
word, but by ear alone we cannot separate a real gain from a better take, and
천우 has said the honest thing: with the delivery still stiff, hearing whether
the timbre moved is hard. So this gives one number per file, on the same
encoder and against the same reference.

Read the ordering, not the value. Absolute cosine similarity moves with the
recording — that is why the earlier speaker-identification work trusts rank
rather than magnitude (docs/speaker-verification.md). Here every file comes
from one source and one reference, so before-versus-after is comparable even
though 0.76 on its own means little.

The stage-1 output is scored too, deliberately. It is the floor: whatever stage
2 gains, it gains over that.

Runs inside ``winter-ai:speaker-embedding``, which bakes the encoder, so this
needs no network:

    docker run --rm --user "$(id -u):$(id -g)" \
        -v "$PWD/review/v23-vc-finetune:/audio:ro" \
        -v "$PWD/experiments:/experiments:ro" \
        --entrypoint python winter-ai:speaker-embedding \
        /experiments/seedvc_similarity.py \
        --reference /audio/03-reference-human.wav /audio/0[012]*.wav
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil

from speechbrain.inference.speaker import EncoderClassifier
import torch
import torchaudio

ENCODER_SAMPLE_RATE = 16000


def load_encoder() -> EncoderClassifier:
    """Load the baked checkpoint without writing into the image.

    speechbrain links its hyperparameters from ``source`` into ``savedir`` and
    then opens them for writing, and huggingface_hub writes lock files beside
    what it reads. Both are read-only in the image, so both move to the tmpfs
    first. Without the cache move this fails looking for the model on the Hub,
    which offline-by-default means it will never find.
    """
    scratch = Path("/tmp/speaker-model")
    if not scratch.exists():
        shutil.copytree(os.environ["SPEAKER_MODEL_DIR"], scratch)
    baked_cache = Path(os.environ.get("SPEAKER_HF_CACHE", "/opt/hf-cache"))
    cache = Path("/tmp/hf-cache")
    if baked_cache.is_dir() and not cache.exists():
        shutil.copytree(baked_cache, cache)
    os.environ["HF_HOME"] = str(cache)
    return EncoderClassifier.from_hparams(source=str(scratch), savedir=str(scratch))


def embed(encoder: EncoderClassifier, path: Path) -> torch.Tensor:
    """One unit voice vector for a whole file.

    Averaging over the file is right here and wrong in the diarization probe:
    there, a file holds several speakers. These files hold one utterance in one
    voice, so the whole thing is the sample.
    """
    waveform, rate = torchaudio.load(str(path))
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if rate != ENCODER_SAMPLE_RATE:
        waveform = torchaudio.functional.resample(waveform, rate, ENCODER_SAMPLE_RATE)
    vector = encoder.encode_batch(waveform).squeeze()
    return vector / vector.norm()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("paths", type=Path, nargs="+")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    encoder = load_encoder()
    reference = embed(encoder, arguments.reference)
    width = max(len(path.name) for path in arguments.paths)
    for path in arguments.paths:
        if path == arguments.reference:
            continue
        similarity = float(torch.dot(embed(encoder, path), reference))
        print(f"{path.name:{width}s}  {similarity:.4f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
