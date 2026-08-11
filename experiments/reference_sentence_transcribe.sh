#!/usr/bin/env bash
# Transcribe each chunk into sentences, not words.
#
# The word-level pass (`-ml 1 -sow`) was right for aligning speakers to words,
# and wrong for finding where sentences end: its cues sit flush against each
# other with no punctuation, so there is nothing to split on. Asking whisper for
# its normal output gives cues that are already sentences, with punctuation and
# their own start and end times.
#
# Output goes beside the word-level VTTs under a distinct name so neither pass
# overwrites the other.
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
[[ -f .env ]] && { set -a; source .env; set +a; }

: "${REFERENCE_STORAGE_ROOT:?Set REFERENCE_STORAGE_ROOT}"
: "${STT_MODEL_DIR:?Set STT_MODEL_DIR}"
PILOT="$REFERENCE_STORAGE_ROOT/derived/audio/stt-pilot"
IMAGE="${STT_IMAGE:-ghcr.io/ggml-org/whisper.cpp:main-vulkan}"
MODEL="${STT_MODEL:-ggml-small.bin}"
SOURCE="${1:-source-003}"

shopt -s nullglob
chunks=("$PILOT/$SOURCE"-*-words.wav)
[[ ${#chunks[@]} -gt 0 ]] || { echo "chunk 오디오가 없습니다: $PILOT" >&2; exit 1; }

for chunk in "${chunks[@]}"; do
  name="$(basename "${chunk%-words.wav}")"
  echo "[$name]"
  # -ng: CPU explicitly. The Vulkan build otherwise picks a device, and this
  # host's driver and the CUDA image disagree about versions.
  docker run --rm --user "$(id -u):$(id -g)" \
    --entrypoint /app/build/bin/whisper-cli \
    -v "$PILOT:/work:rw" \
    -v "$STT_MODEL_DIR:/models:ro" \
    "$IMAGE" \
    -m "/models/$MODEL" \
    -f "/work/$(basename "$chunk")" \
    -l ko -ng -ovtt -of "/work/$name-sentences" >/dev/null
  cues=$(grep -c -- '-->' "$PILOT/$name-sentences.vtt" || true)
  chmod 600 "$PILOT/$name-sentences.vtt"
  echo "  문장 $cues개"
done
