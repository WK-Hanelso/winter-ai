#!/usr/bin/env bash
# Say the same sentences twice — once in the Reference's voice, once in the
# voice the companion has today — and put both where 천우 can listen.
#
# The comparison is the point. "Does it resemble her" is not answerable from a
# single clip; it is answerable from two clips of the same sentence.
#
# Every synthesis failure stops the script. An earlier pipeline in this project
# sent output to /dev/null and ran to completion having produced almost nothing,
# so failures here are loud and fatal.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
[[ -f .env ]] && { set -a; source .env; set +a; }

REVIEW_DIR="${REVIEW_DIR:-review}"
REFERENCE_AUDIO="${REFERENCE_AUDIO:-$REFERENCE_STORAGE_ROOT/artifacts/voice/reference-prompt.wav}"
# Read from a file beside the audio rather than an environment variable: the
# transcript contains quotation marks, and a shell-quoting slip would alter it
# silently. A wrong transcript degrades the cloned voice without any error.
REFERENCE_TEXT_FILE="${REFERENCE_TEXT_FILE:-${REFERENCE_AUDIO%.wav}.txt}"
SENTENCES_FILE="${1:-experiments/voice_sample_sentences.txt}"

[[ -f "$REFERENCE_AUDIO" ]] || { echo "참조 음성이 없습니다: $REFERENCE_AUDIO" >&2; exit 1; }
[[ -f "$REFERENCE_TEXT_FILE" ]] || { echo "참조 대사 파일이 없습니다: $REFERENCE_TEXT_FILE" >&2; exit 1; }
REFERENCE_TEXT="$(cat "$REFERENCE_TEXT_FILE")"
[[ -n "${REFERENCE_TEXT// }" ]] || { echo "참조 대사가 비었습니다: $REFERENCE_TEXT_FILE" >&2; exit 1; }
[[ -f "$SENTENCES_FILE" ]] || { echo "문장 파일이 없습니다: $SENTENCES_FILE" >&2; exit 1; }

mkdir -p "$REVIEW_DIR"
REFERENCE_DIR="$(cd "$(dirname "$REFERENCE_AUDIO")" && pwd)"
OUTPUT_DIR="$(cd "$REVIEW_DIR" && pwd)"
USER_SPEC="$(id -u):$(id -g)"

index=0
while IFS= read -r sentence; do
  [[ -n "${sentence// }" ]] || continue
  index=$((index + 1))
  printf -v tag "%02d" "$index"

  echo "[$tag] $sentence"

  # Cloned: the Reference's voice, conditioned on one clip and its transcript.
  docker run --rm --user "$USER_SPEC" \
    -v "$REFERENCE_DIR:/reference:ro" \
    -v "$OUTPUT_DIR:/output:rw" \
    winter-ai:gpt-sovits \
    --reference-audio "/reference/$(basename "$REFERENCE_AUDIO")" \
    --reference-text "$REFERENCE_TEXT" \
    --text "$sentence" \
    --output-path "/output/s2-$tag-cloned.wav"

  # Today's voice, same sentence, for the side-by-side.
  PYTHONPATH=src python3 - "$sentence" "$OUTPUT_DIR/s2-$tag-current.wav" <<'PYTHON'
import os
import sys
from pathlib import Path

from companion.adapters.melotts import MeloTtsSpeechModel
from companion.contracts import SpeechRequest

model = MeloTtsSpeechModel(
    cache_dir=Path.home() / ".cache" / "melotts-winter",
    user=f"{os.getuid()}:{os.getgid()}",
)
audio = model.synthesize(
    SpeechRequest(text=sys.argv[1], emotion="neutral", pace=1.0, energy=0.5, pitch_offset=0.0)
)
Path(sys.argv[2]).write_bytes(audio.data)
PYTHON

  chmod 600 "$OUTPUT_DIR/s2-$tag-cloned.wav" "$OUTPUT_DIR/s2-$tag-current.wav"
done < "$SENTENCES_FILE"

echo
echo "$index개 문장 × 2가지 목소리 → $REVIEW_DIR"
