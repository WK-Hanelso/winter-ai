#!/usr/bin/env bash
# Build (prompt, response) pairs for one source, chunk by chunk.
#
# Four pinned images take turns, so this is a Host-side driver rather than a
# Python module: whisper.cpp transcribes, Sortformer diarizes, speechbrain says
# which speaker is the Reference, and the dev image assembles the pairs.
#
# Chunking is not only about runtime. Sortformer's memory grows with the square
# of the input length, so a 28 minute file will not fit where five minutes will.
#
# Speaker indices are local to a chunk — speaker 1 here is not speaker 1 in the
# next chunk — so the Reference is identified separately in every chunk from the
# enrolment voice. That is why the speaker probe runs per chunk instead of once.
# No -e: one bad chunk must not end the run. Each step reports and continues.
set -uo pipefail

SOURCE_ID="${1:?source id, e.g. source-003}"
DURATION_SECONDS="${2:?total seconds of the source}"
CHUNK_SECONDS="${3:-300}"

cd "$(dirname "$0")/.."
set -a; . ./.env; set +a
export LOCAL_UID="$(id -u)" LOCAL_GID="$(id -g)"

WORK_HOST="$REFERENCE_STORAGE_ROOT/derived/audio/stt-pilot"
WORK="/reference-data/derived/audio/stt-pilot"
REPORTS="/reference-data/reports"
AUDIO_HOST="$REFERENCE_STORAGE_ROOT/raw/audio/candidate-001/$SOURCE_ID.webm"
ENROL="$WORK/source-004-0-2112"

COMPOSE=(docker compose -f compose.reference-source.yaml)
quiet() { grep -v "^time=\|Container \|No services\|WARNING:\|INFO:" || true; }

for (( START=0; START<DURATION_SECONDS; START+=CHUNK_SECONDS )); do
  LEFT=$(( DURATION_SECONDS - START ))
  LEN=$(( LEFT < CHUNK_SECONDS ? LEFT : CHUNK_SECONDS ))
  # A stub of a chunk carries no usable turn and confuses the speaker split.
  if (( LEN < 60 )); then echo "[$START] ${LEN}s left, too short — stopping"; break; fi
  STEM="$SOURCE_ID-$START-$LEN"
  # Word-level transcription names every artefact with a -words suffix, audio
  # included. Every later step reads those files; looking for the plain names
  # silently found nothing and stopped the run after one chunk.
  WORDS="$STEM-words"
  LOG="$WORK_HOST/$WORDS.build.log"
  echo "=== $STEM ==="

  if [[ ! -f "$WORK_HOST/$WORDS.vtt" ]]; then
    PYTHONPATH=src python3 experiments/reference_transcription_probe.py \
      --audio-path "$AUDIO_HOST" \
      --reference-vtt "$REFERENCE_STORAGE_ROOT/raw/subtitles/candidate-001/$SOURCE_ID.ko-orig.vtt" \
      --model-path "$STT_MODEL_DIR/ggml-small.bin" \
      --work-dir "$WORK_HOST" --source-id "$SOURCE_ID" \
      --start-seconds "$START" --duration-seconds "$LEN" --word-timestamps \
      >"$LOG" 2>&1 || { echo "  전사 실패 — $LOG 참고"; continue; }
  fi

  if [[ ! -f "$REFERENCE_STORAGE_ROOT/reports/spans-$STEM.json" ]]; then
    "${COMPOSE[@]}" run --rm reference-diarization-probe \
      --audio "$WORK/$WORDS.wav" --transcript "$WORK/$WORDS.vtt" \
      --source-id "$SOURCE_ID" --spans-path "$REPORTS/spans-$STEM.json" \
      >>"$LOG" 2>&1 || { echo "  화자 분리 실패 — $LOG 참고"; continue; }
  fi

  REF=$("${COMPOSE[@]}" run --rm reference-speaker-probe \
    --enrolment-audio "$ENROL.wav" --enrolment-vtt "$ENROL.vtt" \
    --target-audio "$WORK/$WORDS.wav" --target-vtt "$WORK/$WORDS.vtt" \
    --target-source-id "$SOURCE_ID" --spans-path "$REPORTS/spans-$STEM.json" 2>&1 \
    | quiet | python3 -c "
import json,sys
raw=sys.stdin.read()
if '{' not in raw:
    print('ERR no output'); raise SystemExit
d=json.loads(raw[raw.index('{'):])
# An error and an unconfident answer must not look the same: one is a bug to
# fix, the other is a chunk with nothing to say.
if d.get('status') == 'error':
    print('ERR ' + str(d['error'])[:80]); raise SystemExit
i = d.get('identified_speaker') or {}
# Guessing the speaker would put the interviewer's words in the Reference's mouth.
print(i['reference_speaker'] if i.get('is_confident') else 'LOW ' + str(i.get('reason','')))
")
  case "$REF" in
    ERR*) echo "  화자 판정 오류: ${REF#ERR }"; continue ;;
    LOW*) echo "  화자 판정 확신 부족: ${REF#LOW }"; continue ;;
  esac
  echo "  Reference = 화자 $REF"

  # Already built. The pair writer refuses to overwrite, so without this a
  # resumed run reports failure on every chunk it finished last time.
  if [[ -f "$REFERENCE_STORAGE_ROOT/derived/transcripts/raw/pairs-$STEM.json" ]]; then
    echo "  이미 생성됨 — 건너뜀"; continue
  fi

  docker compose run --rm -v "$REFERENCE_STORAGE_ROOT:/ref" dev \
    python experiments/reference_exchange_probe.py \
    --words-vtt "/ref/derived/audio/stt-pilot/$WORDS.vtt" \
    --spans-path "/ref/reports/spans-$STEM.json" \
    --reference-speaker "$REF" --source-id "$STEM" \
    --pairs-path "/ref/derived/transcripts/raw/pairs-$STEM.json" \
    --audio-path "/ref/derived/audio/stt-pilot/$WORDS.wav" \
    --clips-dir "/ref/derived/audio/responses" 2>&1 \
    | quiet | python3 -c "
import json,sys
raw=sys.stdin.read()
d=json.loads(raw[raw.index('{'):]) if '{' in raw else {}
print('  쌍', d.get('exchange_count','?'), '| Reference 턴', d.get('reference_turn_count','?'), '| 음성 클립', d.get('response_clips','?'))
" || echo "  쌍 생성 실패"
done
