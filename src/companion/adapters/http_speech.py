"""Stage 1 over HTTP, whichever model is behind it.

Stage 1 says the words and stage 2 makes them 겨울이's, so which model says the
words is meant to be replaceable — that separation is the reason the voice
survived swapping it. Both candidates ended up speaking the same tiny contract,
POST a sentence and receive a wav, so the client is one class and choosing a
stage 1 is choosing a URL.

Whatever was chosen by ear lives in the server, not in the request: speed, the
speaker, the disabled text normalizer. Sending those per call would let two
callers disagree about what she sounds like.
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from companion.adapters.fake import AdapterUnavailableError
from companion.contracts import AudioOutput, SpeechRequest

MEDIA_TYPE = "audio/wav"
# CosyVoice is stage 1 because it is the one that sounds like a person. MeloTTS
# is cleaner by every measure that can be counted — no prompt, so nothing of the
# reference leaks into the output and nothing has to be trimmed back off; on the
# CPU, so the card is free; no mispronunciations in the transcript check — and
# 천우 listened to a whole turn of it and said it sounds like a machine. It stays
# reachable because stage 1 is meant to be swapped, and because the next
# candidate will be judged against both.
COSYVOICE_URL = "http://127.0.0.1:8090"
MELOTTS_URL = "http://127.0.0.1:8092"
STAGE_ONE_URLS = {"cosyvoice": COSYVOICE_URL, "melotts": MELOTTS_URL}


@dataclass(frozen=True)
class HttpSpeechModel:
    base_url: str = COSYVOICE_URL
    timeout_seconds: float = 120.0

    def synthesize(self, request: SpeechRequest) -> AudioOutput:
        text = request.text.strip()
        if not text:
            raise ValueError("cannot synthesize empty text")
        http_request = Request(
            f"{self.base_url.rstrip('/')}/speak",
            data=json.dumps({"text": text}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(http_request, timeout=self.timeout_seconds) as response:
                return AudioOutput(data=response.read(), media_type=MEDIA_TYPE)
        except HTTPError as error:
            detail = error.read().decode("utf-8", "replace")
            raise AdapterUnavailableError(f"목소리 서버가 거절했습니다: {detail}") from error
        except URLError as error:
            raise AdapterUnavailableError(
                f"목소리 서버에 닿지 못했습니다 ({self.base_url}): {error.reason}"
            ) from error
        except TimeoutError as error:
            raise AdapterUnavailableError(
                f"목소리 서버가 {self.timeout_seconds:g}초 안에 답하지 않았습니다"
            ) from error
