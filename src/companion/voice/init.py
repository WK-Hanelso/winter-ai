"""Wait for local Voice services, then emit the single user-ready notification."""

from __future__ import annotations

import argparse
import time
from typing import Callable, Sequence
from urllib.error import URLError
from urllib.request import urlopen

from companion.adapters.pulse_audio import PulseAudioPlayer
from companion.adapters.voice_http import MeloTtsHttpTextToSpeech
from companion.notifications import PrintReadyNotifier, SpokenReadyNotifier
from companion.ports import ReadyNotifier


SERVICES = (("LLM", "http://llm:8080/health"), ("STT", "http://stt:8081/"), ("TTS", "http://tts:8082/health"))


def wait_until_ready(
    *, timeout_seconds: float = 300.0, poll_seconds: float = 2.0,
    request: Callable[[str], bool] | None = None, sleep: Callable[[float], None] = time.sleep,
    write: Callable[[str], None] = print, notifier: ReadyNotifier | None = None,
) -> bool:
    request = request or _is_ready
    deadline = time.monotonic() + timeout_seconds
    pending = dict(SERVICES)
    write("Initializing local Companion services...")
    while pending and time.monotonic() < deadline:
        for name, url in tuple(pending.items()):
            if request(url):
                write(f"[ready] {name}")
                del pending[name]
        if pending:
            sleep(poll_seconds)
    if pending:
        write("Companion unavailable: timed out waiting for " + ", ".join(pending))
        return False
    (notifier or PrintReadyNotifier(write)).notify_ready(
        "Companion is ready. You can start a text or voice conversation."
    )
    return True


def _is_ready(url: str) -> bool:
    try:
        with urlopen(url, timeout=2.0) as response:
            return response.status == 200
    except URLError:
        return False


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, default=300.0)
    parser.add_argument("--notify", choices=("text", "voice"), default="text")
    parser.add_argument("--tts-url", default="http://tts:8082")
    args = parser.parse_args(argv)
    notifier: ReadyNotifier | None = None
    if args.notify == "voice":
        notifier = SpokenReadyNotifier(MeloTtsHttpTextToSpeech(args.tts_url), PulseAudioPlayer())
    return 0 if wait_until_ready(timeout_seconds=args.timeout_seconds, notifier=notifier) else 1


if __name__ == "__main__":
    raise SystemExit(main())
