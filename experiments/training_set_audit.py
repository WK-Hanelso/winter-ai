"""Score every training clip against a trusted set, and drop the ones that fail.

source-004 is a solo broadcast: whatever is in it is the Reference. source-002
and source-003 are conversations, and although each chunk's speaker was
identified confidently, a clip can still carry the other person — the host
agrees on top of her constantly, and a sentence where she holds 80% of the time
still has someone else in the remaining fifth.

Training on that teaches the model a blend of two voices, which is worse than
training on less. So each clip is scored against a centroid built from the
trusted set, and clips below a floor are left out of the combined directory
rather than deleted: being able to look at what was rejected is how we find out
the floor was wrong.

The floor is the widest gap in the lower part of the trusted set's own scores.

Neither a percentile nor a constant works, because the trusted set is not clean:
widening the clip window to what the trainer accepts also admitted two-second
fragments of music and effects from the broadcast. Those score near zero, so
source-004's 5th percentile came out at -0.011 and passed everything.

The Otsu split used for speaker identification does not work either, and it is
worth saying why rather than leaving it as a thing that was tried. It maximises
the distance between the two group means, which suits two speakers of similar
size. Here the junk is a thin tail of about a tenth of the clips, and the split
that maximises that distance is the one isolating the single lowest score — so
it is rejected for leaving a group too small to be a speaker.

What the distribution actually has is a gap: scores run -0.078 to -0.011 for a
tenth of the clips, then jump to 0.217, 0.421 and upward to a median of 0.700.
Finding the widest gap in the bottom quarter lands in it. The trusted set is
filtered by that floor too — it is trusted about who is speaking, not about
whether anyone is.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil

from speechbrain.inference.speaker import EncoderClassifier
import torch
import torchaudio

# The bottom quarter is where a "voice or not" gap can be. Searching the whole
# range would find the spread between her quieter and louder clips instead.
LOWER_SHARE = 0.25

ENCODER_SAMPLE_RATE = 16000
# Sturdier than the mean against the clips that are already contaminated: a few
# blended clips drag a mean toward themselves and raise their own scores.
CENTROID_TRIM = 0.1


def load_encoder() -> EncoderClassifier:
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
    waveform, rate = torchaudio.load(str(path))
    if waveform.shape[0] > 1:
        waveform = waveform.mean(dim=0, keepdim=True)
    if rate != ENCODER_SAMPLE_RATE:
        waveform = torchaudio.functional.resample(waveform, rate, ENCODER_SAMPLE_RATE)
    vector = encoder.encode_batch(waveform).squeeze()
    return vector / vector.norm()


def centroid(vectors: list[torch.Tensor]) -> torch.Tensor:
    """A trimmed mean direction, so a few odd clips cannot define the voice."""
    stacked = torch.stack(vectors)
    rough = stacked.mean(dim=0)
    rough = rough / rough.norm()
    scores = stacked @ rough
    keep = scores >= torch.quantile(scores, CENTROID_TRIM)
    kept = stacked[keep].mean(dim=0)
    return kept / kept.norm()


def widest_gap(scores: list[float]) -> float:
    """A floor in the largest gap among the lowest scores.

    Returns the midpoint of that gap, so the cut sits between the two groups
    rather than on a member of either.
    """
    ordered = sorted(scores)
    limit = max(2, int(len(ordered) * LOWER_SHARE))
    gaps = [
        (ordered[index + 1] - ordered[index], (ordered[index + 1] + ordered[index]) / 2)
        for index in range(limit - 1)
    ]
    return max(gaps)[1]


def seconds(path: Path) -> float:
    info = torchaudio.info(str(path))
    return float(info.num_frames / info.sample_rate)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trusted-dir", type=Path, required=True)
    parser.add_argument("--candidate-dir", type=Path, action="append", default=[])
    parser.add_argument("--output-dir", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    encoder = load_encoder()

    trusted = sorted(arguments.trusted_dir.glob("*.wav"))
    vectors = [embed(encoder, path) for path in trusted]
    voice = centroid(vectors)
    trusted_scores = [float(v @ voice) for v in vectors]
    floor = widest_gap(trusted_scores)
    below = sum(1 for score in trusted_scores if score < floor)
    print(
        f"trusted {len(trusted)}개: 하한 {floor:.3f}, 아래 {below}개 "
        f"(중앙값 {sorted(trusted_scores)[len(trusted_scores) // 2]:.3f})"
    )

    # The trusted set is filtered by its own threshold: it is trusted about who
    # is speaking, not about whether anyone is.
    dropped = sum(seconds(p) for p, s in zip(trusted, trusted_scores, strict=True) if s < floor)
    kept: list[Path] = [p for p, s in zip(trusted, trusted_scores, strict=True) if s >= floor]
    print(
        f"{arguments.trusted_dir.name}: {len(kept)}/{len(trusted)}개 통과 "
        f"(탈락 {len(trusted) - len(kept)}개, {dropped / 60:.1f}분)"
    )
    for directory in arguments.candidate_dir:
        paths = sorted(directory.glob("*.wav"))
        scores = [(path, float(embed(encoder, path) @ voice)) for path in paths]
        passed = [path for path, score in scores if score >= floor]
        dropped_seconds = sum(seconds(p) for p, s in scores if s < floor)
        print(
            f"{directory.name}: {len(passed)}/{len(paths)}개 통과 "
            f"(탈락 {len(paths) - len(passed)}개, {dropped_seconds / 60:.1f}분)"
        )
        for path, score in sorted(scores, key=lambda item: item[1])[:5]:
            mark = "탈락" if score < floor else "통과"
            print(f"    {mark} {score:.3f}  {path.name}")
        kept.extend(passed)

    if arguments.output_dir is not None:
        arguments.output_dir.mkdir(parents=True, exist_ok=True)
        arguments.output_dir.chmod(0o700)
        for path in kept:
            shutil.copy2(path, arguments.output_dir / path.name)
        total = sum(seconds(path) for path in kept)
        print(f"\n합계 {len(kept)}개, {total / 60:.1f}분 → {arguments.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
