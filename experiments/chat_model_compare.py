"""후보 모델들을 같은 질문으로 돌려 답과 속도를 나란히 놓는다.

지금 두뇌는 Qwen3-4B다. 2060의 6 GiB에 맞춰 고른 것을 Orin으로 그대로 들고
왔을 뿐이고, Orin에는 20 GB가 남는다. 그래서 크게 갈 수 있는지와, 한국어로
만들어진 모델이 한국어를 더 낫게 하는지를 같이 본다.

이 스크립트는 고르지 않는다. 속도는 재고, 한국어가 자연스러운지는 천우가 읽고
정한다 — 그건 숫자로 나오지 않는다.

llama-server를 모델마다 새로 띄우는 것은 호출하는 쪽에서 한다. 여기서는 이미
떠 있는 서버 하나에 질문을 던지고 결과를 표로 돌려준다.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# 겨울이가 실제로 받는 종류의 질문들. 길이 제한과 정체성이 걸린 상태에서
# 한국어가 어떻게 나오는지를 보려는 것이므로, 지식 문제는 넣지 않는다.
QUESTIONS = (
    "오늘 뭐 했어?",
    "지금 기분 어때?",
    "나 오늘 좀 힘들었어.",
    "천우라고 알아?",
    "너는 뭘 좋아해?",
    "심심한데 뭐 하지?",
)

# 겨울이가 실제로 받는 system 메시지들. 요약하지 않고 그대로 쓴다 — 처음에는
# 한 줄로 줄여서 쟀는데, 그러면 정체성도 grounding도 빠진 상태를 재는 것이고
# 모델들이 전부 바깥일을 지어냈다. 고르는 근거로는 쓸 수 없는 결과였다.
SYSTEM_PROMPT_FILE = Path(__file__).resolve().parents[1] / "review" / "winter_system_prompt.json"
SAMPLING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.0}


def wait_until_ready(base_url: str, seconds: float = 180.0) -> None:
    """서버가 답할 준비가 될 때까지 기다린다.

    llama.cpp는 모델을 올리는 동안 /health에 503을 준다. curl은 -f 없이는 그것도
    성공으로 끝내기 때문에, 준비되기 전에 질문을 던지고 여섯 번 모두 503을 받는
    일이 실제로 있었다. 여기서는 200만 준비된 것으로 본다.
    """
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with urlopen(f"{base_url.rstrip('/')}/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (HTTPError, URLError, TimeoutError):
            pass
        time.sleep(2)
    raise SystemExit(f"{seconds:g}초 안에 서버가 준비되지 않았습니다: {base_url}")


def ask(base_url: str, question: str, timeout: float,
        system_parts: tuple[str, ...]) -> tuple[str, float, int]:
    body = {
        "messages": [
            *({"role": "system", "content": part} for part in system_parts),
            {"role": "user", "content": question},
        ],
        "stream": False,
        "max_tokens": 300,
        **SAMPLING,
    }
    started = time.time()
    with urlopen(
        Request(
            f"{base_url.rstrip('/')}/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        ),
        timeout=timeout,
    ) as response:
        answer = json.loads(response.read())
    elapsed = time.time() - started
    text = answer["choices"][0]["message"]["content"].strip()
    produced = answer.get("usage", {}).get("completion_tokens", 0)
    return text, elapsed, produced


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8080")
    parser.add_argument("--label", required=True, help="표에 적힐 모델 이름")
    parser.add_argument("--timeout", type=float, default=180.0)
    options = parser.parse_args()

    system_parts = tuple(json.loads(SYSTEM_PROMPT_FILE.read_text(encoding="utf-8")))
    wait_until_ready(options.url)
    print(f"## {options.label}  (system {sum(len(p) for p in system_parts)}자)")
    times: list[float] = []
    tokens = 0
    for question in QUESTIONS:
        text, elapsed, produced = ask(options.url, question, options.timeout, system_parts)
        # 첫 질문은 프롬프트가 캐시되지 않아 느리다. 재는 데는 넣지 않되
        # 버리지도 않는다 — 실제 첫 턴이 그렇기 때문이다.
        times.append(elapsed)
        tokens += produced
        print(f"  천우> {question}")
        print(f"  겨울> {text}    ({elapsed:.2f}초, {produced}토큰)")
    warm = times[1:] or times
    print(f"  -- 첫 턴 {times[0]:.2f}초 | 이후 평균 {sum(warm)/len(warm):.2f}초 "
          f"| 총 {tokens}토큰")


if __name__ == "__main__":
    main()
