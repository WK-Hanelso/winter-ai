"""Say the sample sentences with a fine-tuned flow model.

Same reference clip, same sentences, same averaged speaker vector as the
zero-shot run — only the flow checkpoint differs. Anything else changing would
make the comparison unreadable, and the comparison is the whole point.

The checkpoint is swapped by loading it over the model the normal constructor
built, rather than by pointing the constructor somewhere else: the model
directory holds several files that belong together, and copying a trained flow
into it would leave the pretrained one nowhere.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path
import sys

sys.path.append("/opt/cosyvoice")
sys.path.append("/opt/cosyvoice/third_party/Matcha-TTS")

from cosyvoice.cli.cosyvoice import CosyVoice3  # noqa: E402
import torch  # noqa: E402
import torchaudio  # noqa: E402

MODEL_DIR = "/opt/cosyvoice/pretrained_models/CosyVoice3-0.5B"
PROMPT_RATE = 16_000
INSTRUCT_PREFIX = "You are a helpful assistant.<|endofprompt|>"


def load_trained_flow(model: CosyVoice3, checkpoint: Path) -> None:
    """Replace the flow weights in place, and say how much actually changed."""
    state = torch.load(str(checkpoint), map_location="cpu")
    # Checkpoints saved during training carry more than the weights.
    weights = state.get("model", state)
    before = {name: tensor.clone() for name, tensor in model.model.flow.state_dict().items()}
    missing, unexpected = model.model.flow.load_state_dict(weights, strict=False)
    after = model.model.flow.state_dict()

    changed = sum(
        1 for name, tensor in after.items()
        if name in before and not torch.equal(before[name], tensor)
    )
    print(
        f"flow: {changed}/{len(after)} 텐서가 바뀜"
        f" | 없음 {len(missing)} | 남음 {len(unexpected)}"
    )
    if changed == 0:
        # Loading a checkpoint that changes nothing is the failure that looks
        # like success: synthesis proceeds and sounds exactly like zero-shot.
        raise SystemExit("체크포인트를 불러왔지만 가중치가 하나도 바뀌지 않았습니다.")


def prepare_prompt(path: Path, destination: Path) -> Path:
    speech, rate = torchaudio.load(str(path))
    if speech.shape[0] > 1:
        speech = speech.mean(dim=0, keepdim=True)
    if rate != PROMPT_RATE:
        speech = torchaudio.transforms.Resample(rate, PROMPT_RATE)(speech)
    torchaudio.save(str(destination), speech, PROMPT_RATE)
    return destination


def average_embedding(model: CosyVoice3, clips: Sequence[Path]) -> torch.Tensor:
    vectors = [model.frontend._extract_spk_embedding(str(clip)) for clip in clips]
    stacked = torch.cat(vectors, dim=0)
    mean = stacked.mean(dim=0, keepdim=True)
    return mean / mean.norm(dim=1, keepdim=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--flow-checkpoint", type=Path, required=True)
    parser.add_argument("--clips", type=Path, required=True)
    parser.add_argument("--prompt-audio", type=Path, required=True)
    parser.add_argument("--prompt-text", required=True)
    parser.add_argument("--sentences-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--output-prefix", default="trained")
    parser.add_argument("--speaker-id", default="winter")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    model = CosyVoice3(MODEL_DIR, load_trt=False, load_vllm=False, fp16=False)
    load_trained_flow(model, arguments.flow_checkpoint)

    clips = sorted(arguments.clips.glob("*.wav"))
    if not clips:
        print(f"클립이 없습니다: {arguments.clips}", file=sys.stderr)
        return 1
    averaged = average_embedding(model, clips)

    import tempfile

    staging = tempfile.mkdtemp(prefix="winter-prompt-")
    prompt = prepare_prompt(arguments.prompt_audio, Path(staging) / "prompt16k.wav")
    model.add_zero_shot_spk(
        INSTRUCT_PREFIX + arguments.prompt_text, str(prompt), arguments.speaker_id
    )
    entry = model.frontend.spk2info[arguments.speaker_id]
    for key in ("llm_embedding", "flow_embedding"):
        entry[key] = averaged.to(entry[key].device).to(entry[key].dtype)

    sentences = [
        line.strip()
        for line in arguments.sentences_file.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    arguments.output_dir.mkdir(parents=True, exist_ok=True)
    for index, sentence in enumerate(sentences, start=1):
        pieces = [
            result["tts_speech"]
            for result in model.inference_zero_shot(
                sentence, "", "", zero_shot_spk_id=arguments.speaker_id, stream=False
            )
        ]
        if not pieces:
            print(f"[{index:02d}] 합성 결과가 비었습니다.", file=sys.stderr)
            return 1
        audio = torch.cat(pieces, dim=1)
        path = arguments.output_dir / f"{arguments.output_prefix}-{index:02d}.wav"
        torchaudio.save(str(path), audio, model.sample_rate)
        path.chmod(0o600)
        print(f"[{index:02d}] {path} ({audio.shape[1] / model.sample_rate:.1f}초)  {sentence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
