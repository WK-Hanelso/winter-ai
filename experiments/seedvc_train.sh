#!/usr/bin/env bash
# Fine-tune seed-vc (stage 2) on the Reference's own clips.
#
# The mount is the part that is easy to get wrong. train.py writes to
# `config['log_dir']`, which is `./runs/...` — relative to the working
# directory, not absolute. Mounting the output at /runs looks right, runs to
# completion, prints "Final model saved at ./runs/..." and leaves nothing on
# disk: the checkpoint went into the container's own layer and `--rm` deleted
# it. So the mount goes at $WORKDIR/runs.
#
# Ownership: `--user` keeps the checkpoints owned by the caller rather than
# root, since they land on the private storage volume.
set -euo pipefail

cd "$(dirname "$0")/.."
set -a && . ./.env && set +a

CLIPS="${SEEDVC_CLIPS:-$REFERENCE_STORAGE_ROOT/derived/audio/utterances/source-004-clean}"
RUNS="${SEEDVC_RUNS:-$REFERENCE_STORAGE_ROOT/derived/training/seedvc}"
CHECKPOINTS=/opt/seed-vc/checkpoints/models--Plachta--Seed-VC/snapshots/257283f9f41585055e8f858fba4fd044e5caed6e

if [ ! -d "$CLIPS" ]; then
  echo "clips not found: $CLIPS" >&2
  exit 1
fi
mkdir -p "$RUNS"

# batch-size 2 is what fits the 6 GiB card. num-workers 0 because the dataset is
# ~10 minutes: a loader process costs more than it saves.
docker run --rm --name winter-seedvc-train --gpus all \
  --user "$(id -u):$(id -g)" --shm-size 2g \
  -v "$CLIPS:/data:ro" \
  -v "$RUNS:/opt/seed-vc/runs:rw" \
  -w /opt/seed-vc --entrypoint python winter-ai:seedvc \
  train.py \
  --config "$CHECKPOINTS/config_dit_mel_seed_uvit_whisper_small_wavenet.yml" \
  --pretrained-ckpt "$CHECKPOINTS/DiT_seed_v2_uvit_whisper_small_wavenet_bigvgan_pruned.pth" \
  --dataset-dir /data --run-name "${SEEDVC_RUN_NAME:-winter}" \
  --batch-size 2 --max-steps "${SEEDVC_MAX_STEPS:-1000}" --max-epochs 1000 \
  --save-every "${SEEDVC_SAVE_EVERY:-500}" --num-workers 0

# Verifying rather than trusting the log line: the failure this script exists to
# prevent printed a success message and produced no file.
FINAL="$RUNS/run_dit_mel_seed_uvit_whisper_small_wavenet/${SEEDVC_RUN_NAME:-winter}/ft_model.pth"
if [ ! -f "$FINAL" ]; then
  echo "training reported success but no checkpoint at $FINAL" >&2
  exit 1
fi
echo "checkpoint: $FINAL"
