#!/usr/bin/env bash
# Generate one no-training decision set for the voice architecture.
#
# A: Chatterbox built-in speaker (naturalness ceiling and Seed-VC source)
# B: Chatterbox directly/hybrid conditioned on the Reference
# C: A converted by Seed-VC's explicit F0-conditioned model, with/without pitch adjustment
# D: A converted by the current fine-tuned and pretrained Seed-VC checkpoints
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
[[ -f .env ]] && { set -a; source .env; set +a; }

: "${REFERENCE_STORAGE_ROOT:?Set REFERENCE_STORAGE_ROOT in .env}"
: "${VC_CHECKPOINT:?Set VC_CHECKPOINT in .env}"
: "${VC_CONFIG:?Set VC_CONFIG in .env}"
: "${VC_REFERENCE:?Set VC_REFERENCE in .env}"

SENTENCES="${VOICE_PATH_SENTENCES:-$ROOT_DIR/experiments/voice_path_sentences.tsv}"
OUTPUT="${VOICE_PATH_OUTPUT:-$ROOT_DIR/review/voice-path-bakeoff}"
REFERENCE="$VC_REFERENCE"

for required in "$SENTENCES" "$REFERENCE" "$VC_CHECKPOINT" "$VC_CONFIG"; do
  [[ -f "$required" ]] || { echo "필요한 파일이 없습니다: $required" >&2; exit 1; }
done

mkdir -p "$OUTPUT"
OUTPUT="$(cd "$OUTPUT" && pwd)"
EXPERIMENTS="$ROOT_DIR/experiments"
USER_SPEC="$(id -u):$(id -g)"

echo "1/3 Chatterbox 기본/직접 클론 생성"
docker run --rm --gpus all --user "$USER_SPEC" \
  -e HOME=/cache -e HF_HOME=/cache/huggingface -e NUMBA_CACHE_DIR=/cache/numba \
  -e MPLCONFIGDIR=/cache/matplotlib -e TORCH_HOME=/cache/torch \
  -v "${CHATTERBOX_CACHE:-$HOME/.cache/winter-ai/chatterbox}:/cache" \
  -v "$EXPERIMENTS:/probe:ro" \
  -v "$SENTENCES:/sentences.tsv:ro" \
  -v "$REFERENCE_STORAGE_ROOT:$REFERENCE_STORAGE_ROOT:ro" \
  -v "$OUTPUT:/output:rw" \
  --entrypoint python winter-ai:chatterbox-v3 \
  /probe/chatterbox_reference_compare.py \
    --reference "$REFERENCE" \
    --sentences /sentences.tsv \
    --out /output

sources=()
for name in s1 q1 s2 q2 s3 q3; do
  [[ -f "$OUTPUT/$name.wav" ]] || { echo "Chatterbox 결과가 없습니다: $name.wav" >&2; exit 1; }
  sources+=("/output/$name.wav")
done

echo "2/3 Seed-VC F0 보존 경로 생성"
docker run --rm --gpus all --user "$USER_SPEC" \
  -v "$EXPERIMENTS:/probe:ro" \
  -v "$REFERENCE_STORAGE_ROOT:$REFERENCE_STORAGE_ROOT:ro" \
  -v "$OUTPUT:/output:rw" \
  -w /opt/seed-vc --entrypoint python winter-ai:seedvc \
  /probe/seedvc_f0_compare.py \
    --reference "$REFERENCE" --out /output "${sources[@]}"

echo "3/3 현재 fine-tuned Seed-VC와 pretrained 기준선 생성"
docker run --rm --gpus all --user "$USER_SPEC" \
  -v "$EXPERIMENTS:/probe:ro" \
  -v "$REFERENCE_STORAGE_ROOT:$REFERENCE_STORAGE_ROOT:ro" \
  -v "$OUTPUT:/output:rw" \
  -w /opt/seed-vc --entrypoint python winter-ai:seedvc \
  /probe/seedvc_pretrained_compare.py \
    --checkpoint "$VC_CHECKPOINT" --config "$VC_CONFIG" \
    --reference "$REFERENCE" --out /output "${sources[@]}"

chmod 600 "$OUTPUT"/*.wav
echo
echo "완료: $OUTPUT"
echo "직접/혼합 클론=__직접/__혼합-*, F0 보존=__f0맞춤/__f0그대로, 현재 모델=__우리것"
