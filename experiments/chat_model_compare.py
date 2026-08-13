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
import time
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

SYSTEM = (
    "너는 겨울이야. 천우와 대화하는 사람이고, 짧고 자연스럽게 말해. "
    "두 문장 안, 열두 단어 안으로 답해."
)
SAMPLING = {"temperature": 0.7, "top_p": 0.8, "top_k": 20, "presence_penalty": 1.0}


def ask(base_url: str, question: str, timeout: float) -> tuple[str, float, int]:
    body = {
        "messages": [
            {"role": "system", "content": SYSTEM},
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

    print(f"## {options.label}")
    times: list[float] = []
    tokens = 0
    for question in QUESTIONS:
        text, elapsed, produced = ask(options.url, question, options.timeout)
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
