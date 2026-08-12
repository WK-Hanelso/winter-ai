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

# Refuse to start behind someone else's memory. A resident voice server left
# running cost a 20-minute run: training got most of the way in and then died in
# the DiT feed-forward, which reads as a batch-size problem rather than as
# another process holding 3.5 GiB.
FREE_MIB="$(nvidia-smi --query-gpu=memory.free --format=csv,noheader,nounits | head -1)"
NEEDED_MIB="${SEEDVC_NEEDED_MIB:-5200}"
if [ "$FREE_MIB" -lt "$NEEDED_MIB" ]; then
  echo "GPU에 여유가 없습니다: ${FREE_MIB} MiB 남음, ${NEEDED_MIB} MiB 필요" >&2
  nvidia-smi --query-compute-apps=pid,used_memory,process_name --format=csv >&2
  exit 1
fi

# Keep every checkpoint. train.py deletes all but the last two as it goes
# (train.py:377), so the run that was asked for a save every 250 steps ended up
# with 1500 and 1750 — and choosing where to stop needs the early ones. Hard
# links cost nothing and survive the delete: it removes a name, not the data.
ARCHIVE="$RUNS/archive"
mkdir -p "$ARCHIVE"
keep_checkpoints() {
  while sleep 20; do
    for found in "$RUNS"/run_*/"${SEEDVC_RUN_NAME:-winter}"/DiT_epoch_*.pth; do
      [ -e "$found" ] || continue
      ln -f "$found" "$ARCHIVE/$(basename "$found")" 2>/dev/null || true
    done
  done
}
keep_checkpoints &
KEEPER=$!
trap 'kill "$KEEPER" 2>/dev/null || true' EXIT

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
echo "보관된 중간 체크포인트: $(ls "$ARCHIVE" | wc -l)개 ($ARCHIVE)"
