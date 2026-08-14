#!/usr/bin/env bash
# 한 소스를 원본 오디오에서 학습용 클립까지 끝까지 민다.
#
# 손으로 돌리던 다섯 단계 — chunk 자르기, 단어 전사, 문장 전사, 화자 분리,
# 화자 판정, 문장 절단 — 를 한 줄로 묶는다. 555분치를 손으로 돌릴 수는 없다.
#
# 이미 있는 결과는 건너뛴다. 밤새 도는 작업이고 중간에 끊길 수 있으므로,
# 다시 실행하면 하던 곳에서 이어야 한다.
#
# 화자 판정이 안 된 chunk는 버린다. 추측해서 자르면 다른 사람 목소리가
# 학습에 들어가고, 그건 소리로 바로 나타나지 않아서 나중에 원인을 못 찾는다.
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
[[ -f .env ]] && { set -a; source .env; set +a; }

: "${REFERENCE_STORAGE_ROOT:?Set REFERENCE_STORAGE_ROOT}"
: "${STT_MODEL_DIR:?Set STT_MODEL_DIR}"

SOURCE="${1:?소스 ID가 필요합니다 (예: source-005)}"
CHUNK_SECONDS="${CHUNK_SECONDS:-300}"
STT_MODEL="${STT_MODEL:-ggml-small.bin}"
STT_IMAGE="${STT_IMAGE:-ghcr.io/ggml-org/whisper.cpp:main-vulkan}"
# 등록용은 겨울이가 가장 많이 나오는 소스 전체(56%)를 쓴다. 이름을 지어내지 말 것 —
# 없는 파일을 가리켜서 화자 판정이 전부 실패했고, 그러면 전사를 아무리 돌려도
# 클립이 하나도 안 나온다.
# 3분이면 충분하다. 35분짜리로 재면 chunk당 190초, 3분이면 120초인데 판정은
# 같았다 (source-002-600-300에서 화자 1, margin 0.1982 대 기존 0.2537).
# chunk마다 다시 도는 단계라 그 70초가 130번 쌓인다.
ENROL_AUDIO="${ENROL_AUDIO:-/reference-data/derived/audio/stt-pilot/enrol-short.wav}"
ENROL_VTT="${ENROL_VTT:-/reference-data/derived/audio/stt-pilot/enrol-short.vtt}"

PILOT="$REFERENCE_STORAGE_ROOT/derived/audio/stt-pilot"
REPORTS="$REFERENCE_STORAGE_ROOT/reports"
AUDIO="$REFERENCE_STORAGE_ROOT/raw/audio/candidate-001/$SOURCE.webm"
[[ -f "$AUDIO" ]] || { echo "원본이 없습니다: $AUDIO" >&2; exit 1; }

total=$(ffprobe -v error -show_entries format=duration -of csv=p=0 "$AUDIO")
total=${total%.*}
echo "[$SOURCE] 길이 ${total}초, ${CHUNK_SECONDS}초씩 처리합니다"

start=0
while [[ $start -lt $total ]]; do
  remaining=$(( total - start ))
  length=$(( remaining < CHUNK_SECONDS ? remaining : CHUNK_SECONDS ))
  [[ $length -lt 30 ]] && break          # 30초 미만 꼬리는 버린다
  chunk="$SOURCE-$start-$length"
  echo "  [$chunk]"

  # 1. chunk 자르기 + 단어 전사
  # SKIP_TRANSCRIBE=1이면 전사는 Orin이 맡는다. 2060 CPU로 같은 chunk를 다시
  # 돌리면 9배 느린 쪽이 GPU가 이미 끝낸 일을 반복한다.
  if [[ "${SKIP_TRANSCRIBE:-0}" != "1" && ! -f "$PILOT/$chunk-words.vtt" ]]; then
    # 호스트에서 돌린다. 이 도구는 자기가 docker를 불러 ffmpeg와 whisper를
    # 띄우므로, 컨테이너 안에 넣으면 docker가 없어서 죽는다.
    #
    # chunk 이름은 도구가 source-id와 구간으로 직접 만든다. 여기서 만든 이름을
    # 넘기면 source-013-0-300-0-300 처럼 겹친다.
    PYTHONPATH=src python3 experiments/reference_transcription_probe.py \
      --audio-path "$REFERENCE_STORAGE_ROOT/raw/audio/candidate-001/$SOURCE.webm" \
      --reference-vtt "$PILOT/$chunk-words.vtt" \
      --model-path "$STT_MODEL_DIR/$STT_MODEL" \
      --work-dir "$PILOT" \
      --source-id "$SOURCE" --start-seconds "$start" --duration-seconds "$length" \
      --word-timestamps >/dev/null 2>&1 || echo "    단어 전사 실패"
  fi

  # 2. 문장 전사 — 절단 경계는 여기서 온다
  if [[ "${SKIP_TRANSCRIBE:-0}" != "1" && ! -f "$PILOT/$chunk-sentences.vtt" && -f "$PILOT/$chunk-words.wav" ]]; then
    docker run --rm --user "$(id -u):$(id -g)" \
      --entrypoint /app/build/bin/whisper-cli \
      -v "$PILOT:/work:rw" -v "$STT_MODEL_DIR:/models:ro" "$STT_IMAGE" \
      -m "/models/$STT_MODEL" -f "/work/$chunk-words.wav" \
      -l ko -ng -ovtt -of "/work/$chunk-sentences" >/dev/null 2>&1 || echo "    문장 전사 실패"
  fi

  # 3. 화자 분리
  if [[ ! -f "$REPORTS/spans-$chunk.json" && -f "$PILOT/$chunk-words.vtt" ]]; then
    docker compose -f compose.reference-source.yaml run --rm --no-deps \
      reference-diarization-probe \
        --audio "/reference-data/derived/audio/stt-pilot/$chunk-words.wav" \
        --transcript "/reference-data/derived/audio/stt-pilot/$chunk-words.vtt" \
        --source-id "$chunk" \
        --report-path "/reference-data/reports/diarization-$chunk.json" \
        --spans-path "/reference-data/reports/spans-$chunk.json" >/dev/null 2>&1 \
      || echo "    화자 분리 실패"
  fi

  # 4. 어느 화자가 겨울이인지
  if [[ ! -f "$REPORTS/who-is-reference-$chunk.json" && -f "$REPORTS/spans-$chunk.json" ]]; then
    docker compose -f compose.reference-source.yaml run --rm --no-deps \
      reference-speaker-probe \
        --enrolment-audio "$ENROL_AUDIO" --enrolment-vtt "$ENROL_VTT" \
        --target-audio "/reference-data/derived/audio/stt-pilot/$chunk-words.wav" \
        --target-vtt "/reference-data/derived/audio/stt-pilot/$chunk-words.vtt" \
        --target-source-id "$chunk" \
        --spans-path "/reference-data/reports/spans-$chunk.json" \
        --report-path "/reference-data/reports/who-is-reference-$chunk.json" >/dev/null 2>&1 \
      || echo "    화자 판정 실패"
  fi

  start=$(( start + length ))
done

# 5. 문장 단위 절단. 학습용이므로 1~30초 — 3~10초는 참조 음성 기준이고,
#    그 폭으로 자르면 쓸 수 있는 말의 상당수가 버려진다.
echo "[$SOURCE] 클립 절단"
docker run --rm -u "$(id -u):$(id -g)" \
  -v "$REFERENCE_STORAGE_ROOT":"$REFERENCE_STORAGE_ROOT" -v ./experiments:/probe:ro \
  --entrypoint python winter-ai:chatterbox /probe/reference_utterance_clips.py \
    --storage-root "$REFERENCE_STORAGE_ROOT" --source "$SOURCE" \
    --min-seconds 1.0 --max-seconds 30.0 \
    --report "$REPORTS/utterances-$SOURCE-wide.json" 2>&1 | grep -E "발화|건너뜀|저장"
