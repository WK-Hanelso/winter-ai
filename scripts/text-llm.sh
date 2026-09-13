#!/usr/bin/env bash
# Host-only lifecycle for the measured Docker hybrid profile. Never contacts Orin.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME=winter-text-llm
PROFILE=rtx2060-hybrid-v1
URL=http://127.0.0.1:18081
CTX_SIZE=${WINTER_TEXT_CTX_SIZE:-8192}
IMAGE=${WINTER_TEXT_LLM_IMAGE:-winter-ai:rtx2060-llama-8e7f22b}
MODEL=${WINTER_TEXT_MODEL_PATH:-$ROOT_DIR/data/research/rtx2060-hybrid/model/Qwen3-30B-A3B-Instruct-2507-Q4_K_M.gguf}

fail() { echo "$*" >&2; exit 1; }
healthy() { curl -fsS --max-time 2 "$URL/health" >/dev/null 2>&1; }
inspect_owned() {
  local metadata
  metadata=$(docker container inspect --format '{{index .Config.Labels "io.winter.text-runtime"}}|{{index .Config.Labels "io.winter.project-root"}}' "$NAME" 2>/dev/null) || return 1
  [[ "$metadata" == "$PROFILE|$ROOT_DIR" ]] || fail "동명 컨테이너의 소유권이 다릅니다. 변경하지 않았습니다: $NAME"
}

# Serialize start/stop across terminals; status and help never load a model.
docker info >/dev/null 2>&1 || fail "Docker에 연결할 수 없어 모델 상태를 확인하지 못했습니다."
case "${1:-}" in
  start|stop)
    mkdir -p "$ROOT_DIR/data"
    exec 9>"$ROOT_DIR/data/.text-llm.lock"
    flock -w 130 9 || fail "다른 텍스트 모델 시작/종료 작업이 진행 중입니다."
    ;;
  status)
    if inspect_owned; then
      if healthy; then echo "Host 텍스트 모델: ready ($URL)"
      else echo "Host 텍스트 모델: not ready ($URL)"; fi
    else echo "Host 텍스트 모델: stopped"; fi
    exit 0 ;;
  *) fail "Usage: bash scripts/text-llm.sh {start|stop|status}" ;;
esac

if [[ "$1" == stop ]]; then
  if inspect_owned; then
    docker stop --time 10 "$NAME" >/dev/null
    docker rm "$NAME" >/dev/null
    echo "Host 텍스트 모델을 종료했습니다. 대화·기억·모델 파일은 보존했습니다."
  else echo "Host 텍스트 모델은 이미 종료되어 있습니다."; fi
  exit 0
fi

if inspect_owned; then
  if [[ "$(docker container inspect --format '{{.State.Running}}' "$NAME")" == true ]]; then
    if healthy; then echo "Host 텍스트 모델: ready ($URL)"; exit 0; fi
  else
    # Only remove this launcher's stopped, ephemeral server; no data volume is writable.
    docker rm "$NAME" >/dev/null
  fi
fi
if ! docker container inspect "$NAME" >/dev/null 2>&1; then
  healthy && fail "18081 포트에 다른 서버가 있습니다. 기존 서버를 변경하지 않았습니다."
  [[ -f "$MODEL" && -s "$MODEL" ]] || fail "텍스트 모델 파일이 없습니다: $MODEL (WINTER_TEXT_MODEL_PATH로 지정 가능)"
  [[ "$MODEL" == /* ]] || MODEL="$ROOT_DIR/$MODEL"
  image_id=$(docker image inspect --format '{{.Id}}' "$IMAGE" 2>/dev/null) || fail "텍스트 모델 실행 이미지가 없습니다: $IMAGE"
  echo "Host RTX2060 + RAM에서 겨울이 모델을 준비합니다. Orin은 사용하지 않습니다."
  docker run -d --name "$NAME" --pull never --gpus all --network host --read-only \
    --label "io.winter.text-runtime=$PROFILE" --label "io.winter.project-root=$ROOT_DIR" \
    --cap-drop ALL --security-opt no-new-privileges --pids-limit 128 \
    --memory 20g --memory-swap 20g --cpus 4 --tmpfs /tmp:rw,nosuid,nodev,size=128m \
    -e GGML_CUDA_DISABLE_GRAPHS=1 --log-opt max-size=5m --log-opt max-file=2 \
    --mount "type=bind,src=$MODEL,dst=/model.gguf,readonly" \
    "$image_id" --model /model.gguf --n-gpu-layers 99 --n-cpu-moe 44 \
    --ctx-size "$CTX_SIZE" --parallel 1 --threads 4 --threads-batch 4 \
    --reasoning-budget 0 \
    --batch-size 128 --ubatch-size 32 --host 127.0.0.1 --port 18081 >/dev/null
fi

for ((attempt=0; attempt<60; attempt++)); do
  [[ "$(docker container inspect --format '{{.State.Running}}' "$NAME")" == true ]] || \
    fail "모델 시작 실패. docker logs --tail 30 $NAME 으로 확인할 수 있습니다."
  if healthy; then
    echo "Host 텍스트 모델 준비 완료. 첫 답변은 문맥 처리 때문에 더 오래 걸릴 수 있습니다."
    exit 0
  fi
  if (( attempt % 10 == 0 )); then echo "모델 로딩 중… ($((attempt * 2))초 경과)"; fi
  sleep 2
done
docker stop --time 10 "$NAME" >/dev/null
fail "모델 준비 시간 초과로 서버를 중단했습니다. 로그: docker logs --tail 30 $NAME"
