#!/usr/bin/env bash
# 겨울이의 두뇌는 Orin에 있고, 이 터널이 그리로 가는 유일한 길이다.
#
# Orin의 llama-server는 127.0.0.1에만 묶여 있다. Orin은 공개 IP로 SSH가 열려
# 있어서, LLM 포트를 인터페이스에 직접 걸면 인증 없는 엔드포인트가 인터넷에
# 열린다. 그래서 노출은 늘리지 않고 SSH 위로만 지나간다.
#
# 재연결 반복이 있는 이유는 단순하다. 이 터널이 죽으면 겨울이는 말을 못 한다.
# ssh는 연결이 끊기면 그냥 종료하므로, 다시 붙는 것은 여기가 맡는다.
set -u

ORIN_HOST=${ORIN_HOST:-armstrong@14.34.75.232}
ORIN_PORT=${ORIN_PORT:-5555}
LOCAL_PORT=${LOCAL_PORT:-18080}
REMOTE_PORT=${REMOTE_PORT:-8080}

while true; do
    ssh -N \
        -o BatchMode=yes \
        -o ExitOnForwardFailure=yes \
        -o ServerAliveInterval=30 \
        -o ServerAliveCountMax=3 \
        -L "${LOCAL_PORT}:127.0.0.1:${REMOTE_PORT}" \
        -p "${ORIN_PORT}" "${ORIN_HOST}"
    # 여기 도달했다는 것은 터널이 끊겼다는 뜻이다. Orin 재부팅 중일 수도 있으니
    # 곧바로 다시 시도하되, 실패가 이어질 때 로그를 채우지 않도록 잠깐 쉰다.
    echo "[$(date '+%H:%M:%S')] 터널이 끊겼습니다. 5초 후 재연결합니다." >&2
    sleep 5
done
