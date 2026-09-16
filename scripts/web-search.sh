#!/usr/bin/env bash
# Local SearXNG for Winter web search (P8). start|stop|status. Bound to 127.0.0.1:8888 only.
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME=winter-search
IMAGE=${WINTER_SEARCH_IMAGE:-searxng/searxng:latest}
PORT=${WINTER_SEARCH_PORT:-8888}
healthy() { curl -fsS --max-time 2 "http://127.0.0.1:$PORT/healthz" >/dev/null 2>&1; }
case "${1:-status}" in
  start)
    if healthy; then echo "검색 서버가 이미 떠 있습니다 (127.0.0.1:$PORT)."; exit 0; fi
    docker rm -f "$NAME" >/dev/null 2>&1 || true
    docker run -d --name "$NAME" --pull never \
      -p "127.0.0.1:$PORT:8080" \
      --cap-drop ALL --cap-add CHOWN --cap-add SETGID --cap-add SETUID \
      --security-opt no-new-privileges --pids-limit 256 --memory 1g \
      --log-opt max-size=5m --log-opt max-file=2 \
      -v "$ROOT_DIR/deploy/searxng/settings.yml:/etc/searxng/settings.yml:ro" \
      "$IMAGE" >/dev/null
    for _ in $(seq 1 30); do healthy && { echo "검색 서버 준비 완료 (127.0.0.1:$PORT)."; exit 0; }; sleep 1; done
    echo "검색 서버가 30초 안에 준비되지 않았습니다." >&2; docker logs --tail 20 "$NAME" >&2; exit 1 ;;
  stop) docker rm -f "$NAME" >/dev/null 2>&1 && echo "검색 서버를 내렸습니다." || echo "검색 서버가 없습니다." ;;
  status) healthy && echo "검색 서버 정상 (127.0.0.1:$PORT)" || { echo "검색 서버 없음"; exit 1; } ;;
  *) echo "usage: $0 {start|stop|status}" >&2; exit 2 ;;
esac
