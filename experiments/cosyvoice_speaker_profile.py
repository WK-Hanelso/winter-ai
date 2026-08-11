"""Build the Reference's speaker vector from every clip, not from one.

Zero-shot cloning describes a speaker with a single x-vector taken from the
prompt clip. Eight seconds of one sentence is a narrow view of a person: 천우
heard the result as close but thin, "음역이 아쉽다" — the pitch range is not
all there. It would not be, from one sentence.

So the vector is averaged over all the clips instead. The model keeps using one
clip as its acoustic prompt, because that carries rhythm and room and has to
come from a single continuous piece of speech, but *who* it thinks is talking
now comes from twelve minutes rather than eight seconds.

This is not training. It changes one tensor, runs on the CPU, and takes about as
long as reading the clips. It is worth doing before fine-tuning because if the
thinness was only ever the single-clip view, no training was needed to fix it.

The averaged speaker is written into the model directory as a named speaker, so
synthesis afterwards just names it.
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


def average_embedding(model: CosyVoice3, clips: Sequence[Path]) -> torch.Tensor:
    """Mean x-vector over the clips, renormalised.

    Paths, not tensors: the extractor loads the file itself and resamples to the
    16 kHz its front end wants. Handing it audio already loaded fails, and
    handing it 48 kHz samples would have produced confident nonsense.

    The mean of unit vectors is not a unit vector, and every other embedding the
    model handles is normalised. Skipping that would quietly change the scale of
    the conditioning as well as its direction.
    """
    vectors = []
    for clip in clips:
        vectors.append(model.frontend._extract_spk_embedding(str(clip)))
        print(".", end="", flush=True)
    print()
    stacked = torch.cat(vectors, dim=0)
    mean = stacked.mean(dim=0, keepdim=True)
    return mean / mean.norm(dim=1, keepdim=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clips", type=Path, required=True, help="평균낼 클립 디렉터리.")
    parser.add_argument("--prompt-audio", type=Path, required=True, help="음향 참조로 쓸 클립.")
    parser.add_argument("--prompt-text", required=True, help="그 클립에서 실제로 말한 내용.")
    parser.add_argument("--speaker-id", default="winter")
    # Several sentences in one run, because the registration lives in the
    # process: each `docker run` starts from the packaged spk2info again, so
    # synthesising one sentence per run would discard the average every time.
    parser.add_argument("--sentences-file", type=Path, help="한 줄에 한 문장.")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--output-prefix", default="profile")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    clips = sorted(arguments.clips.glob("*.wav"))
    if not clips:
        print(f"클립이 없습니다: {arguments.clips}", file=sys.stderr)
        return 1

    model = CosyVoice3(MODEL_DIR, load_trt=False, load_vllm=False, fp16=False)

    print(f"클립 {len(clips)}개에서 화자 벡터를 평균내는 중")
    averaged = average_embedding(model, clips)

    # Register the speaker from the acoustic prompt first, so every other field
    # the model needs is filled in the way it expects, then replace only the
    # identity. Building the dict by hand would mean guessing its shape.
    model.add_zero_shot_spk(
        INSTRUCT_PREFIX + arguments.prompt_text,
        str(arguments.prompt_audio),
        arguments.speaker_id,
    )
    entry = model.frontend.spk2info[arguments.speaker_id]
    single = entry["llm_embedding"]
    similarity = float(
        torch.nn.functional.cosine_similarity(single, averaged.to(single.device))
    )
    for key in ("llm_embedding", "flow_embedding"):
        entry[key] = averaged.to(entry[key].device).to(entry[key].dtype)
    model.save_spkinfo()

    print(f"화자 '{arguments.speaker_id}' 등록 | 한 클립 벡터와의 유사도 {similarity:.4f}")
    print(f"저장: {MODEL_DIR}/spk2info.pt")

    if arguments.sentences_file is None or arguments.output_dir is None:
        return 0

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
            print(f"[{index:02d}] 합성 결과가 비었습니다: {sentence}", file=sys.stderr)
            return 1
        audio = torch.cat(pieces, dim=1)
        path = arguments.output_dir / f"{arguments.output_prefix}-{index:02d}.wav"
        torchaudio.save(str(path), audio, model.sample_rate)
        path.chmod(0o600)
        print(f"[{index:02d}] {path} ({audio.shape[1] / model.sample_rate:.1f}초)  {sentence}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
